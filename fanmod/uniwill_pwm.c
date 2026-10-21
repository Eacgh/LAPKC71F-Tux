// SPDX-License-Identifier: GPL-2.0
/*
 * Minimal Uniwill / NUC X15 fan PWM interface.
 *
 * The EC's writeable PWM registers (0x1804 / 0x1809) are only reachable via
 * the WMI GetSetULong method, not through the ACPI ECRW window used by the
 * in-tree uniwill driver.  This module exposes a tiny /dev/uniwill_pwm node
 * for the NUC Center helper.
 *
 * Safety:
 *  - the module refuses to load unless reading EC 0x0740 returns 0x08
 *    (the project ID of this NUC X15)
 *  - every PWM write rearms a 5 second watchdog
 *  - when the watchdog fires, or the device is closed, or the module is
 *    unloaded, the EC is put back into its automatic fan mode (0x0751 = 0)
 *  - writes use 0x0751 = 0x40 first and then 0x1804/0x1809 = 0..200, which is
 *    the same sequence used by the qc71_laptop driver
 *
 * Writing to EC registers is inherently risky.  Keep the safety fallbacks.
 */

#include <linux/acpi.h>
#include <linux/delay.h>
#include <linux/fs.h>
#include <linux/jiffies.h>
#include <linux/kernel.h>
#include <linux/miscdevice.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/string.h>
#include <linux/sysfs.h>
#include <linux/slab.h>
#include <linux/uaccess.h>
#include <linux/wmi.h>
#include <linux/workqueue.h>

#define DRIVER_NAME "uniwill_pwm"
#define UNIWILL_WMI_GUID "ABBC0F6F-8EA1-11D1-00A0-C90629100000"
#define UNIWILL_WMI_GETSETULONG 4
#define WATCHDOG_MS 5000

static int wmi_instance = -1;
static struct mutex pwm_lock;
static struct delayed_work pwm_watchdog;
static struct miscdevice pwm_misc;
static u8 last_pwm1;
static u8 last_pwm2;
static bool pwm_active;
static atomic_t open_count = ATOMIC_INIT(0);

static int ec_xfer(u16 addr, u16 data, u8 *out, bool read)
{
	u8 input_buf[8] = {
		addr & 0xFF,
		(addr >> 8) & 0xFF,
		data & 0xFF,
		(data >> 8) & 0xFF,
		0,
		read ? 1 : 0,
		0,
		0,
	};
	u8 output_buf[sizeof(union acpi_object) + 40]
		__aligned(__alignof__(union acpi_object)) = { 0 };
	struct acpi_buffer input = { sizeof(input_buf), input_buf };
	struct acpi_buffer output = { sizeof(output_buf), output_buf };
	union acpi_object *obj;
	acpi_status status;

	if (wmi_instance < 0)
		return -ENODEV;

	status = wmi_evaluate_method(UNIWILL_WMI_GUID, wmi_instance,
				     UNIWILL_WMI_GETSETULONG, &input, &output);
	if (ACPI_FAILURE(status))
		return -EIO;

	obj = output.pointer;
	if (!obj || obj->type != ACPI_TYPE_BUFFER || obj->buffer.length < 1)
		return -ENODATA;

	if (out)
		*out = ((u8 *)obj->buffer.pointer)[0];
	return 0;
}

static int ec_read8(u16 addr, u8 *value)
{
	return ec_xfer(addr, 0, value, true);
}

static int ec_write8(u16 addr, u8 value)
{
	return ec_xfer(addr, value, NULL, false);
}

static int ec_auto_locked(void)
{
	int ret = ec_write8(0x0751, 0x00);

	pwm_active = false;
	return ret;
}

static int ec_set_pwm_locked(u8 fan1, u8 fan2)
{
	int ret;

	/* The qc71 manual sequence: enter fan-boost/manual mode, then PWM. */
	ret = ec_write8(0x0751, 0x40);
	if (ret)
		return ret;
	ret = ec_write8(0x1804, fan1);
	if (ret)
		goto restore;
	ret = ec_write8(0x1809, fan2);
	if (ret)
		goto restore;

	last_pwm1 = fan1;
	last_pwm2 = fan2;
	pwm_active = true;
	mod_delayed_work(system_wq, &pwm_watchdog, msecs_to_jiffies(WATCHDOG_MS));
	return 0;

restore:
	ec_auto_locked();
	return ret;
}

static void pwm_watchdog_fn(struct work_struct *work)
{
	mutex_lock(&pwm_lock);
	if (pwm_active) {
		pr_info(DRIVER_NAME ": watchdog fired, restoring automatic fans\n");
		ec_auto_locked();
	}
	mutex_unlock(&pwm_lock);
}

static int pwm_open(struct inode *inode, struct file *file)
{
	if (atomic_inc_return(&open_count) != 1) {
		atomic_dec(&open_count);
		return -EBUSY;
	}
	return 0;
}

static int pwm_release(struct inode *inode, struct file *file)
{
	/* Cancel before taking the lock: the work may be waiting for it. */
	cancel_delayed_work_sync(&pwm_watchdog);
	mutex_lock(&pwm_lock);
	ec_auto_locked();
	mutex_unlock(&pwm_lock);
	atomic_dec(&open_count);
	return 0;
}

static ssize_t pwm_write(struct file *file, const char __user *user_buf,
			 size_t count, loff_t *ppos)
{
	char buf[64];
	size_t len = min(count, sizeof(buf) - 1);
	unsigned int fan1, fan2;
	int ret;

	if (copy_from_user(buf, user_buf, len))
		return -EFAULT;
	buf[len] = '\0';

	if (sysfs_streq(buf, "auto") || strncmp(buf, "auto", 4) == 0) {
		cancel_delayed_work_sync(&pwm_watchdog);
		mutex_lock(&pwm_lock);
		ret = ec_auto_locked();
		mutex_unlock(&pwm_lock);
		return ret < 0 ? ret : count;
	}

	if (sscanf(buf, "%u %u", &fan1, &fan2) != 2)
		return -EINVAL;
	fan1 = min(fan1, 200u);
	fan2 = min(fan2, 200u);

	mutex_lock(&pwm_lock);
	ret = ec_set_pwm_locked((u8)fan1, (u8)fan2);
	mutex_unlock(&pwm_lock);
	return ret < 0 ? ret : count;
}

static ssize_t pwm_read(struct file *file, char __user *user_buf,
			size_t count, loff_t *ppos)
{
	char buf[64];
	int len;

	mutex_lock(&pwm_lock);
	len = scnprintf(buf, sizeof(buf), "%u %u %u\n", last_pwm1, last_pwm2,
			pwm_active ? 1u : 0u);
	mutex_unlock(&pwm_lock);
	return simple_read_from_buffer(user_buf, count, ppos, buf, len);
}

static const struct file_operations pwm_fops = {
	.owner = THIS_MODULE,
	.open = pwm_open,
	.release = pwm_release,
	.read = pwm_read,
	.write = pwm_write,
	.llseek = noop_llseek,
};

static int __init uniwill_pwm_init(void)
{
	u8 project = 0;
	int instance;
	int ret;

	mutex_init(&pwm_lock);
	INIT_DELAYED_WORK(&pwm_watchdog, pwm_watchdog_fn);

	/* The GUID is duplicated; find the instance that answers as the EC. */
	for (instance = 0; instance < 16; instance++) {
		wmi_instance = instance;
		ret = ec_read8(0x0740, &project);
		if (!ret && project == 0x08)
			break;
	}
	if (instance == 16) {
		pr_err(DRIVER_NAME ": no Uniwill EC WMI instance found\n");
		return -ENODEV;
	}
	pr_info(DRIVER_NAME ": using WMI instance %d, project 0x%02x\n",
		instance, project);

	pwm_misc.minor = MISC_DYNAMIC_MINOR;
	pwm_misc.name = DRIVER_NAME;
	pwm_misc.fops = &pwm_fops;
	pwm_misc.mode = 0600;
	ret = misc_register(&pwm_misc);
	if (ret)
		return ret;

	mutex_lock(&pwm_lock);
	ec_auto_locked();
	mutex_unlock(&pwm_lock);

	pr_info(DRIVER_NAME ": /dev/%s ready\n", DRIVER_NAME);
	return 0;
}

static void __exit uniwill_pwm_exit(void)
{
	cancel_delayed_work_sync(&pwm_watchdog);
	mutex_lock(&pwm_lock);
	ec_auto_locked();
	mutex_unlock(&pwm_lock);
	misc_deregister(&pwm_misc);
	pr_info(DRIVER_NAME ": unloaded, automatic fans restored\n");
}

module_init(uniwill_pwm_init);
module_exit(uniwill_pwm_exit);

MODULE_AUTHOR("NUC Center");
MODULE_DESCRIPTION("Uniwill NUC X15 fan PWM via WMI");
MODULE_LICENSE("GPL");
