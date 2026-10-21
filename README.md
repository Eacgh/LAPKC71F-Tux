# NUC Center

一个为 **Intel NUC X15 Laptop Kit（LAPKC71F）** 写的 Windows 风格系统管理器。
界面为中文，使用 GTK3 + Cairo；监控数据来自 Linux hwmon，风扇/灯带通过
Uniwill EC（`acpi_call`），RGB 键盘通过 ITE 8291 HID 完成。

## 这台电脑是什么型号？

从 DMI / ACPI / hwmon 读到的信息：

| 项目 | 值 |
| --- | --- |
| 产品名称 | **LAPKC71F** |
| 品牌 | Intel(R) Client Systems |
| 产品版本 | M38718-406 |
| SKU | BKC71FBFU6000 |
| 主板 | LAPKC71F / M22222-404 |
| BIOS | KCTGL357.0051.2024.1218.1352（2024-12-18） |
| CPU | Intel Core i7-11800H（8 核 16 线程，最高 4.6 GHz） |
| 独显 | NVIDIA GeForce RTX 3070 Mobile / Max-Q（GA104M） |
| 核显 | Intel UHD Graphics（TigerLake-H） |
| 内存 | 46 GiB（2× SO-DIMM） |
| 电池 | 94 Wh（设计 8200 mAh，当前满充 5825 mAh） |
| 屏幕 | 15.6" 2560×1440 165 Hz |
| 键盘 | RGB 背光机械键盘 |
| 灯条 | 前部 RGB Light Bar |
| EC | Uniwill，内核 `uniwill` 驱动 |

这是 2021 年发布的 **Intel NUC X15 Laptop Kit**，Uniwill 代工，和
XMG / TUXEDO / Maibenben X568 等机器共享大部分 EC 设计。

## 功能

### Overview
- CPU / GPU 温度双环形仪表
- 主/副风扇转速、PWM 占空比
- CPU 负载、内存、电池、充电状态
- 最近 90 秒温度曲线
- 快捷开关：系统性能模式、键盘亮度、80%/100% 充电限制

### Cooling
- 两个风扇的实时 RPM 和 PWM
- **Quiet / Balanced / Performance** 三种固件性能模式
  - 同时改变 EC 风扇曲线、NVIDIA TGP / Dynamic Boost、模式指示灯
  - 对应 Windows 版 NUC Studio 的三个性能档
- **Maximum cooling**（风扇 Boost）
- **自动散热曲线**：默认 5 个“温度 → 风扇百分比”点，线性插值；不需要手工设置
  - 温度先做低通平滑，输出百分比每秒最多 +4%（85°C 以上 +8%）、-2%，实现缓升缓降
  - 最低 10%；80/85/90°C 有安全下限，90°C 立即 100%
  - 通过实验性 WMI PWM 模块实现连续 0–200 PWM；5 秒看门狗，helper 停止写入会自动恢复
- CPU 每个核心的温度
- 温度 + 风扇转速历史曲线
- NVMe / Wi-Fi / ACPI 温度

### Lighting
- **RGB 键盘**：亮度 0–4 级 + RGB 颜色（ITE 8291 HID，已实机验证）
- **前部灯条**：亮度、RGB 颜色、彩虹动画、休眠呼吸灯
- 实时键盘 + 灯条预览
- 8 个预设颜色 + 自定义取色器

### Battery
- 电量、充电状态、功率、剩余时间
- **充电上限**（建议长期插电用 80%）
- 健康度 / 损耗 / 设计容量 / 满充容量 / 电压 / 电流 / 循环次数

### Settings
- FN Lock、Super 键、触控板切换开关
- 温度单位（°C / °F）、深色/浅色主题、刷新间隔
- 开机自启动
- 特权 helper 状态、重连、复制安装命令
- 设备信息与系统报告

## 安装

系统要求：Arch Linux / Plasma（Wayland 或 X11）、GTK3、PyGObject、python-cairo。
`install.sh` 会自动安装缺少的 `python-cairo`。

```bash
cd ~/桌面/nuc/nuc-center
sudo ./install.sh
sudo ./fanmod/install.sh   # 实验性 WMI PWM 风扇曲线模块
nuc-center
```

`fanmod/install.sh` 会安装 `gcc`、`make`、`linux-headers`，编译并加载
`uniwill_pwm`，然后重启 helper。内核更新后需要重新运行一次。

安装脚本会：

1. 安装 `python-cairo`（如果缺少）。
2. 询问是否安装 `acpi_call`（风扇性能模式/Fan Boost 需要）。
3. 安装 GUI 到 `/usr/local/lib/nuc-center`，命令为 `/usr/local/bin/nuc-center`。
4. 给 `wheel` 组开放 ITE 8291 键盘的 `/dev/hidraw` 设备。
5. 安装并启动 `nuc-center-helper.service`（root 权限的小型 helper）。
6. 安装桌面图标和 `.desktop` 文件。

如果只想先看监控、不启用控制：

```bash
sudo ./install.sh --without-ec
nuc-center
```

或者直接从源码运行（开发模式）：

```bash
cd ~/桌面/nuc/nuc-center
python3 -m nuc_center --self-test   # 只读传感器，不开窗口
python3 -m nuc_center               # 需要 python-cairo、显示会话
```

## 权限与安全

GUI 本身**不以 root 运行**。所有需要特权的操作都通过
`/run/nuc-center/helper.sock` 发给 systemd 管理的 root helper：

```text
GUI (user) ──Unix socket──> nucd.py (root) ──sysfs / ACPI EC──> hardware
```

- Socket 权限为 `root:wheel 0660`，只有 `wheel` 组用户能连接。
- Helper 只接受固定动作（设置灯、设置键盘、设置性能模式、设置电池上限等），
  **不接受任意寄存器地址或任意文件路径**。
- EC 写入只涉及 Uniwill 公开文档/内核驱动中已知的寄存器。
- 性能模式会修改 GPU TGP 和功耗墙，日常建议用 Balanced。
- 电池上限建议 80%，不要长期 100% 满电存放。

## 工作原理 / 技术细节

- `uniwill` 内核驱动暴露：
  - `/sys/class/hwmon/hwmonX/`：CPU/GPU 温度、风扇 RPM、PWM 读数
  - `/sys/class/leds/uniwill:multicolor:status/`：前部灯条 RGB
  - `/sys/class/power_supply/BAT0/charge_control_end_threshold`：充电上限
  - `/sys/devices/platform/INOU0000:00/`：FN Lock、Super 键、触控板、彩虹/呼吸动画
- 当前内核（7.2.x）还没有把 NUC X15 的键盘 RGB 暴露成 LED，也没有
  `platform_profile`。因此本程序通过 `acpi_call` 调用 `INOU0000` 的
  `ECRR` / `ECRW` ACPI 方法，直接操作 EC：
  - `0x0751`：固件性能模式 / 风扇 Boost
    - Quiet `0x10`，Balanced `0x20`，Performance `0x30`
    - Fan Boost `0x40`（bit 6，持续有效；写入后风扇约 6000 RPM）
  - `0x0769–0x076B` / `0x078C`：部分 Uniwill 机型的内置键盘寄存器
- NUC X15 的 RGB 键盘实际上是一个独立的 USB HID 设备：
  - `048d:6006 ITE Device(8291)`（`/dev/hidraw3`）
  - 通过 9 字节 HID feature report 设置亮度（0–50）和 6×21 行 RGB 数据
  - 不走主 EC，也不创建 `kbd_backlight` LED
- 所有 EC 访问都串行化，并在每次读写后等待 6 ms，模拟 OEM 软件的节奏。
### 实验性 WMI 风扇模块（`fanmod/`）

- 设备：`/dev/uniwill_pwm`，写入 `"<fan1> <fan2>"`（0–200）或 `"auto"`
- 写入序列：`0x0751 = 0x40` → `0x1804` / `0x1809` = PWM
- 平滑：温度低通滤波 + 输出限速（+4%/s，85°C 以上 +8%/s，-2%/s）
- 恢复自动：`0x0751 = 0x00`
- 模块加载时检查 EC `0x0740 == 0x08`，只在本机 LAPKC71F 上工作
- 5 秒看门狗；fd 关闭、helper 停止、模块卸载都会恢复自动
- 实测 PWM 50/100/150/200 对应风扇约 1730/3369/4759/5989 RPM
- 编译需要 `gcc make linux-headers`；内核更新后重新运行
  `sudo ./fanmod/install.sh`

- 实测记录（本机 LAPKC71F）：
  - 三个性能模式命令都会被 EC 接受；模式位会在几十毫秒后自动清零，
    所以程序会缓存最后一次选择，避免界面立刻跳回 Balanced。
  - Fan Boost 写入 `0x40` 后双风扇从约 1500 RPM 升到约 6000 RPM，
    写 `0x00` 恢复。
  - 键盘 RGB、亮度 0–4、前部灯条 RGB、充电上限、FN Lock、彩虹动画
    均已在实机验证。
  - 机身性能键切换模式时，EC 不会把新模式留在可读寄存器里；此时界面
    可能仍显示上一次在程序里选择的模式，重新选择一次即可。

### 文件结构

```text
nuc-center/
├── nuc_center/
│   ├── app.py          # 主窗口、轮询、导航、主题、自启动
│   ├── pages.py        # 概览 / 散热 / 灯效 / 电池 / 设置
│   ├── widgets.py      # Cairo 仪表、风扇、图表、键盘预览、Toast
│   ├── sensors.py      # hwmon / proc / 电池 / DMI 读取
│   ├── controls.py     # GUI 侧的 helper 客户端 / 只读模式
│   ├── priv.py         # root helper 的固定动作后端
│   ├── ec.py           # ACPI ECRR/ECRW、寄存器解码
│   ├── ite.py          # ITE 8291 RGB 键盘 hidraw 协议
│   ├── theme.py        # 深色/浅色调色板和 GTK CSS
│   └── config.py       # ~/.config/nuc-center/config.json
├── nucd.py             # root helper daemon
├── fanmod/
│   ├── uniwill_pwm.c   # 实验性 WMI PWM 内核模块
│   ├── Makefile
│   └── install.sh
├── tools/
│   └── test-keyboard.py # ITE 8291 颜色测试
├── install.sh
├── uninstall.sh
└── nuc-center.desktop
```

## 常见问题

**界面没有仪表/图表，终端刷 `Couldn't find foreign struct converter for 'cairo.Context'`**

缺少 `python-cairo`：

```bash
sudo pacman -S python-cairo
```

**Cooling 页面显示 “EC performance control is unavailable”**

`acpi_call` 没有安装或没有加载：

```bash
sudo pacman -S acpi_call
sudo modprobe acpi_call
systemctl restart nuc-center-helper
```

如果刚更新过内核但还没重启，模块版本可能不匹配；重启后再试。

**键盘 RGB 不生效**

NUC X15 的键盘是 `048d:6006 ITE Device(8291)`，不是内核 LED，也不走
Uniwill EC。检查设备是否存在：

```bash
lsusb | grep -i 048d
ls -l /dev/hidraw*
```

安装脚本会给 `wheel` 组开放这个 hidraw 设备。程序通过特权服务在
`/dev/hidrawN` 上发送 HID feature/output report；如果 helper 还在用旧代码，
重启一次：

```bash
sudo systemctl restart nuc-center-helper
```

灯条本身由内核 LED 驱动，和键盘是两套独立硬件。安装完成后可以先跑一次
颜色测试：

```bash
cd ~/桌面/nuc/nuc-center
python3 tools/test-keyboard.py
```

如果提示找不到设备，用 `lsusb | grep 048d` 确认控制器存在。

**风扇能看不能手动调速？**

本机 EC 通过 ACPI ECRW 只暴露“自动/全速”两档；连续 PWM 在 WMI 的
`0x1804/0x1809` 寄存器里。安装 `fanmod/install.sh` 后，程序通过
`/dev/uniwill_pwm` 设置 0–200 PWM，散热页面会出现“自定义风扇曲线”。

模块带 5 秒看门狗：helper 停止写入、崩溃或被关闭后，会自动写
`0x0751 = 0x00` 恢复 EC 自动风扇。`fanmod/install.sh` 还会把模块安装到
`/usr/lib/modules/$(uname -r)/extra/` 并写入
`/etc/modules-load.d/nuc-center-pwm.conf`。

注意：这仍然是实验性接口，内核更新后要重新编译；如果只想保守使用，
不安装 `fanmod` 即可，程序会回退到 EC 自动/全速两档。

**查看 helper 日志**

```bash
journalctl -u nuc-center-helper.service -b -f
```

**打印 EC 状态**

```bash
sudo python3 /usr/local/lib/nuc-center/nucd.py --once
```

**卸载**

```bash
sudo ./uninstall.sh
```

`acpi_call` 不会被自动删除（可能还有其他程序在用），需要的话：

```bash
sudo pacman -Rns acpi_call
```

## 已知限制

- 仅针对 Uniwill EC / LAPKC71F 调好；其他 Uniwill 机器可以监控，但控制项
  可能不同。
- 键盘 RGB 通过 ITE 8291 HID 控制，不会创建 `kbd_backlight` LED；
  如果内核未来提供标准 LED 接口，程序会优先使用它。
- 性能模式/Fan Boost/风扇曲线依赖 `acpi_call` + `uniwill_pwm`；键盘 RGB 依赖 ITE 8291 hidraw。
- 性能模式寄存器会自清零，程序缓存最后一次选择；按机身性能键切换时
  界面可能不会立即同步。
- 充电上限的可用范围由 EC 决定（本机支持 1–100%，界面限制在 40–100%）。
- **连续 PWM 风扇曲线是实验性的**：需要 `fanmod` 内核模块走 WMI 的
  `0x1804/0x1809`；主线/qc71 驱动都提示这两个寄存器可能不稳定。模块已加
  5 秒看门狗和高温安全下限，但仍不建议在无人看管的重要任务中使用。
- 内核更新后 `uniwill_pwm.ko` 需要重新编译（重新运行 `sudo ./fanmod/install.sh`）。

## 许可

本项目代码可自由修改和使用。EC 寄存器定义参考了 Linux 内核
`uniwill-laptop` 驱动和 `qc71_laptop` 驱动；感谢 Armin Wolf、Werner Sembach、
Pőcze Barnabás 以及 TUXEDO Computers 的工作。
