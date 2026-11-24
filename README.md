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
- **Quiet / Balanced / Performance** 三档整机模式（对应 Windows NUC Studio）
  - CPU PL1/PL2：30/60、45/65、65/90 W（来自 EC 默认 PL 表）
  - 显卡 TGP：80 / 100 / 125 W
  - 模式灯：3 / 2 / 1；模式灯同时会强制风扇位（1 = Turbo）
  - 选择保存到 `/var/lib/nuc-center/power-profile`，helper 启动时重放
- **显卡功耗上限 80 / 100 / 125 W**（EC `0x0743–0x0746`）
  - Linux 默认只有 80 W；Windows 的 Balanced / Performance 分别是 100 / 125 W
  - 写入后立即生效，`nvidia-smi` 的 Current Power Limit 会随之变化
  - `tools/gpu-power.py` 也可以直接从命令行读写
  - 选择会记录到 `/var/lib/nuc-center/gpu-power`；安装并启动 helper 后，
    每次开机都会重新应用（BIOS 会把 `0x0743` 重置）
- **Maximum cooling**（风扇 Boost）
- **自动散热曲线**：5 个“温度 → 风扇百分比”点，线性展开成 EC 的 15 点表
  - 写入 EC RAM（`0xF00–0xF5F`）后由 EC 自主执行，不需要内核模块
  - duty = 百分比 × 2；槽 0 的 duty `0x00` 启用表，`0xFF` 交回 EC 自动
  - 关闭曲线会清表并恢复 EC 自动；helper 退出时 EC 继续执行最后一张表，
    不会卡在手动 PWM
  - 80/85/90°C 的安全下限仍由 UI 侧计算，建议最后一点在 80°C 左右到 100%
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
nuc-center
```

风扇曲线走 EC 表，随主程序安装，**不需要** `fanmod`。只有想尝试 WMI 连续
PWM 时才需要额外运行 `sudo ./fanmod/install.sh`（会装 `gcc`/`make`/
`linux-headers` 并编译 `uniwill_pwm`，内核更新后要重跑）。

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
- 性能模式只切换风扇曲线和模式指示灯，不改变显卡功耗；GPU 功耗由单独的
  “显卡功耗上限”动作写入 EC 的 `0x0743–0x0746`。
- 125 W 会让显卡和供电明显更热，日常建议 100 W；写之前确认散热良好。
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
  - `0x0751`：固件风扇模式 / 风扇 Boost
    - Balanced `0x00`，Performance `0x10`（Windows 实读；`0x10` 是 Turbo 位）
    - 本固件没有独立的 Quiet 位；安静曲线由 EC 风扇表实现，界面上的
      “安静”按钮被禁用
    - Fan Boost `0x40`（bit 6，持续有效；写入后风扇约 6000 RPM）
  - `0x0F00–0x0F5F`：EC 风扇表（16 槽，槽 0 固定 `20/17/0`）
    - `0xF00` CPU 上升温度、`0xF10` CPU 下降温度、`0xF20` CPU duty
    - `0xF30` / `0xF40` / `0xF50` 是 GPU 的对应三张表
    - duty = 百分比 × 2（0–200）；槽 0 `0x00` 启用表，`0xFF` 表示 EC 自动
    - `0x07C5` bit7 = 双风扇独立表；`0x07C6` bit2 = 启用表引擎
    - `0x0751` bit6 清零 = 手动模式；`0x0741` bit0 = AP OEM 手动控制
  - `0x0730/0x0731`、`0x0734/0x0735`、`0x07A7/0x07A8` 是 EC 自带的三组
    PL1/PL2 默认表（30/60、45/65、65/90 W）
  - `0x0783/0x0784` 写当前 PL1/PL2；`0x07AB` 低 4 位是模式灯，
    并且会强制 `0x0751` 的风扇位（1=Turbo），所以自定义曲线启用时
    模式灯保持在 Standard(2)
  - `0x0743–0x0746`：显卡功耗（TGP + Dynamic Boost）
    - `0x0743`：bit0 公共使能、bit1 Dynamic Boost、bit2 TGP 偏移
    - `0x0744`：CTGP 偏移（W）；`0x0745`：DB 配套值 45；`0x0746`：DB 瓦数
    - 80 W = `0x01 / 0 / 0 / 0`，100 W = `0x03 / 0 / 45 / 20`，
      125 W = `0x07 / 25 / 45 / 20`
    - 有效 TGP = 80 + CTGP + Dynamic Boost。NVIDIA 驱动只读 EC 的结果，
      所以 `nvidia-smi -pl` 在这台机器上无效
  - `0x0769–0x076B` / `0x078C`：部分 Uniwill 机型的内置键盘寄存器
- NUC X15 的 RGB 键盘实际上是一个独立的 USB HID 设备：
  - `048d:6006 ITE Device(8291)`（`/dev/hidraw3`）
  - 通过 9 字节 HID feature report 设置亮度（0–50）和 6×21 行 RGB 数据
  - 不走主 EC，也不创建 `kbd_backlight` LED
- 所有 EC 访问都串行化，并在每次读写后等待 6 ms，模拟 OEM 软件的节奏。
### 可选：实验性 WMI 风扇模块（`fanmod/`）

EC 风扇表是默认路径；`fanmod` 不再需要，只在需要瞬时全速 PWM 或
在其他 Uniwill 机型上做实验时才用。

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
  - **显卡功耗墙**：Linux 默认 `0x0743 = 0x02`（未使能），`nvidia-smi` 报 80 W。
    写入 100 W 后 Current Power Limit 立即变成 100 W；写入 125 W 后请求值为
    125 W（Current 会随 Dynamic Boost 在 105–125 W 之间浮动）。不需要重启，
    也不需要重载 NVIDIA 驱动。
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
│   ├── test-keyboard.py # ITE 8291 颜色测试
│   └── gpu-power.py     # 只读/设置 EC 显卡功耗上限（含回滚）
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

**GPU 一直只有 80 W，`nvidia-smi -pl` 也改不动？**

这台机器的功耗墙归 EC 管，`nvidia-smi -pl` 会被 vBIOS 拒绝。先装 `acpi_call`，
再用散热页的“显卡功耗上限”，或者命令行：

```bash
sudo pacman -S --needed acpi_call
sudo modprobe acpi_call
sudo python3 tools/gpu-power.py status   # 只读，确认 0x0741 是 0x01
sudo python3 tools/gpu-power.py set 100  # 80 / 100 / 125
nvidia-smi -q -d POWER | grep 'Current Power Limit'
sudo python3 tools/gpu-power.py restore  # 回滚到写入前的值
```

写入后立即生效，不需要重启。没装 NUC Center 的 helper 时，重启后 BIOS 会把
`0x0743` 重置，需要再跑一次 `set`。如果 EC 值变了但 `nvidia-smi` 不动，先跑几秒
GPU 负载；仍然不动就重启一次，或在启动早期写入（EC 的值可能需要 GPU 上电
时才被驱动读取）。

**风扇能看不能手动调速？**

默认走 EC 风扇表：散热页的“自动散热曲线”会写入 `0xF00–0xF5F`，由 EC
自主执行，不需要内核模块。曲线是 5 个 UI 点线性展开成 15 个阈值点，
duty = 百分比 × 2；关闭曲线会清表并恢复 EC 自动。

`fanmod` 是旧的可选路径：它用 WMI 的 `0x1804/0x1809` 做连续 0–200 PWM，
适合实验或需要瞬时全速，但内核更新后要重编，且部分机型不稳定。

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
- 性能模式/Fan Boost/风扇曲线依赖 `acpi_call`；风扇曲线走 EC 表，`uniwill_pwm` 只是可选回退。键盘 RGB 依赖 ITE 8291 hidraw。
- 本固件没有独立的 Quiet 风扇位；界面上的“安静”按钮被禁用，安静效果
  可以用自定义 EC 风扇曲线实现。
- **显卡功耗上限只有 80 / 100 / 125 W 三档**（145 W 仅保留给命令行工具做短时
  跑分）；125 W 会让整机明显更热，需要确认风扇和硅脂状态。
- 性能模式寄存器会自清零，程序缓存最后一次选择；按机身性能键切换时
  界面可能不会立即同步。
- 充电上限的可用范围由 EC 决定（本机支持 1–100%，界面限制在 40–100%）。
- **EC 风扇表最多 15 个点**：界面 5 个点会线性展开；曲线满转的爬升比
  Fan Boost 慢（约 20–30 秒到高转），需要瞬时全速时用“最大散热”。
- 自定义 EC 曲线启用时，模式灯固定在 Standard(2)，模式切换只改
  CPU PL / GPU TGP，不改风扇；关闭曲线后模式会重新接管风扇和模式灯。
- `fanmod` 的 WMI PWM 是可选实验路径；主线/qc71 驱动都提示 `0x1804/0x1809`
  可能不稳定。

## 许可

本项目代码可自由修改和使用。EC 寄存器定义参考了 Linux 内核
`uniwill-laptop` 驱动和 `qc71_laptop` 驱动；感谢 Armin Wolf、Werner Sembach、
Pőcze Barnabás 以及 TUXEDO Computers 的工作。
