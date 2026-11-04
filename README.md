# Preloader_Rw

> **实测范围：只在 vivo 的 MTK 平台设备上跑通过。** 其他厂商或其他平台的机型
> 没有验证过，它们的架构可能不一样（分区布局、签名流程、DA 版本），用之前请先
> 自行确认。

MTK（联发科）平台 Preloader / DA（Download Agent）阶段的刷写与读取工具，  
带 PySide6 图形界面。主机侧用 Python 实现 DA1/DA2 协议栈，设备端始终运行上传固件。

## 工作原理

按官方下载流程引导设备并建立命令通道：把设备切进下载态（fastboot  
`oem reboot-edl` 或设备已在 preloader 态）→ 上传 DA1 → 供出 DA2 →  
签名认证 → 读取分区表 → 移交命令面。之后的分区读取、刷写、擦除都走  
DA2 的原生命令回合。

流程由**载荷**（工作流脚本，默认 `preloader_rw/payloads/route.py`）驱动，  
载荷可整体替换（`PANE_PAYLOAD=<你的脚本>`，契约见  
`preloader_rw/core/kernel/api.py`，最小示例  
`preloader_rw/payloads/minimal.py`），内核一行不用改。

内核能认哪些命令由**命令表**决定（默认
`preloader_rw/profiles/mtk_official.json`）：命令名、拼帧方式、回合内可能
出现的 `<info>` / `<key>`、注册探针、传输符号全在表里，内核代码里没有任何
命令名字面量。换一份 DA 固件 = 换表 + 换载荷，见「命令表」一节。

**可能的认证一条路**：DA(n) 的受限命令需要服务器签名的应答，设备在认证回合  
开口索取时才向用户实时要（GUI 弹窗 / JSON 宿主事件），不是启动前提，  
仓库内没有任何预置、绕过或降级路径。

## 支持平台

- 芯片：MTK（联发科）preloader/DA 架构的设备。
- 实测：**只在 vivo 的 MTK 平台机型上验证过**（MT6895/MT6896 类 UFS）。
  其他厂商、其他平台的设备架构可能不同，未做验证，需自行确认后再用。
- 主机：Windows 10/11 x64，Python 3.10+。
- 传输：libusb（`preloader_rw/backends/libusb/` 自带 `libusb-1.0.dll`）  
  或 WinUSB（`preloader_rw/backends/winusb/driver/` 自带 `mtk_edl.inf` 安装包）。

## 命令表（换 DA 固件时改这里）

内核不硬编码任何命令符号。启动时它读一张表，据此判断设备上行帧属于哪一类、
按名字取命令并拼帧、绑回合里出现的 `<info>` / `<key>`、用哪条命令裁决注册。
默认表随仓库发布（34 条命令）：

    preloader_rw/profiles/mtk_official.json

换自己的 DA 固件 = 换表 + 换载荷，内核代码一行不改（目前只在 vivo 的 DA 上
验证过；换其他厂商的固件，表要按那边实际的命令面自己写）：

    set "PANE_TABLE=C:\my\my_da2.json"
    set "PANE_PAYLOAD=C:\my\my_route.py"

GUI 资源页第四行「命令表」与上面两行等价；不选即用内置表。表在**预检**阶段
装载并校验（schema、构造器是否存在、模板引用的参数是否已声明、探针命令是否
登记过），坏了在打开设备之前就以 `BAD_TABLE` 报出来。

| 段 | 作用 |
|---|---|
| `transport` | 帧分类用的命令符（START / END / DOWNLOAD-FILE / UPLOAD-FILE / 文件查询） |
| `probe` | 注册裁决探针：命令、设备侧缓冲、期望字节数 |
| `commands[]` | 每条命令：`name` `phase` `args` `build` `pulls` `pushes` `fs_keys` `levels` |
| `filesys` / `boot` / `handover` | 文件查询键名、BOOT-TO 地址、2nd-DA 的 `<info>` 别名 |

`build` 有两种写法，可混用：

    "build": "read_flash"                             具名构造器（core/kernel/commands.py 的 BUILDERS）
    "build": {"template": "<command>…{partition}…"}   纯数据模板，写表不必碰 Python

带条件元素或嵌套 XML 的命令（`SET-BOOT-MODE`、`DEBUG:UFS`、`WRITE-PARTITION`
的 `pre_erase`）只能用具名构造器；模板只做扁平替换，`{name:08X}` 这类格式
说明符照 Python `str.format` 走。

改完先离线自检，不用接设备：

    python scripts\check_table.py                    # 内置表
    python scripts\check_table.py my_da2.json        # 自己的表
    python scripts\check_table.py my_da2.json --frames

换表时要同时换载荷：载荷决定「发什么、按什么顺序发」，表决定「内核认识哪些
命令、怎么拼」。两者对不上时，命令会在提交那一刻以 `UnknownCommand` 拒绝，
而不是发出一个设备看不懂的帧。

## 安装与配置

1. **装 Python**：Windows x64 的 Python 3.10+（勾选加入 PATH）。
2. **装依赖**：在本目录执行 `pip install -r requirements.txt`。  
   启动器会挑一个带 PySide6 的解释器；找不到会用可读文字说明缺什么。
3. **准备 DA 镜像**：DA1/DA2 是厂商专有二进制，**本仓库不附带**，必须  
   自行提供。GUI 资源页原位选择，或  
   `copy scripts\config.example.bat config.bat`（config.bat 放仓库根）  
   填入 `PANE_DA1` / `PANE_DA2`。缺镜像不会启动失败，`fire` 时返回可读  
   的 `CONFIG` 错误，补上再发即可。
4. **装驱动**：见下节。
5. **签名应答**：无需提前准备任何东西；设备索取时 GUI 会弹窗，选择或  
   粘贴签好名的应答即可。

全部配置项都是 `PANE_*` 环境变量（见 `scripts/config.example.bat` 与  
`preloader_rw/relay/assembly.py` 的注释），仓库内不保存任何本机绝对路径。

**目标设备 USB ID 可配**：默认匹配 VID `0x0E8D` / PID `0x2000`  
（MediaTek preloader）。设备枚举 ID 不同时，设  
`PANE_USB_VID` / `PANE_USB_PIDS`（十进制或 `0x` 十六进制，多 PID 用  
`;` 分隔），并在 `preloader_rw/backends/winusb/driver/mtk_edl.inf` 里加  
对应的 HardwareID 行后再装驱动。

## 驱动安装

Windows 上 libusb 只能打开已绑定 WinUSB（或 libusbK）驱动的设备；  
设备没有任何驱动时枚举得到的是「已插入 · 驱动未接管」。两条路：

- **仓库自带 INF**：管理员 PowerShell 执行  
  `preloader_rw/backends/winusb/driver/install.ps1`  
  （或刷写页状态行出现「驱动未接管」时点旁边的「安装驱动」按钮，  
  会经 UAC 提权运行同一脚本）。INF 只匹配 `USB\VID_0E8D&PID_2000`，  
  复用系统自带 winusb.sys，不改写其他设备绑定。
- **Zadig**：把设备绑到 WinUSB，效果相同，适合 INF 装不上的机器。

## 常见问题

- **INF 装不上**：`mtk_edl.inf` 未签名。启用了强制驱动签名的机器上  
  `pnputil /add-driver` 会拒绝——需要先开测试签名模式  
  （`bcdedit /set testsigning on` 后重启），或用 Zadig。
- **提示需要管理员权限**：pnputil 必须管理员权限；脚本检测到会给出  
  可读错误并以非零退出。装完需要重新插拔设备才生效。
- **枚举显示「被占用」**：设备被其他程序占用（如另一个刷机工具），  
  关掉占用方即可，不是驱动问题。
- **刷写页状态行**：实时显示设备枚举结果——`未插入` / `已插入 ·
  驱动未接管` / `已插入 · 被占用或无权限` / `可打开 <vid>:<pid>`，  
  状态来自真实 USB 枚举，不写死。

## 启动

```bat
scripts\启动程序.bat
```

（等价于 `scripts\launch_gui.bat` → 挑选带 PySide6 的解释器 → 加载可选  
`config.bat` → `pythonw -m preloader_rw.client`。）

GUI 页面：资源（DA 镜像与载荷）、刷写（设备枚举状态行、分区表、  
逐分区读/写/擦除；初始显示仓库自带的分区表，设备 GPT 读回后替换）、  
日志、运行记录。

命令行后端（调试/脚本）：`python -m preloader_rw.relay --json`  
（stdin/stdout 的 JSON Lines）。

## 目录结构

| 目录                       | 职责                                                                                                |
| ------------------------ | ------------------------------------------------------------------------------------------------- |
| `preloader_rw/`          | 主包，全部 Python 代码都在这一个命名空间下                                                                         |
| `preloader_rw/core/`     | 内核：proto（DA/XML 协议）→ engine（命令循环）→ kernel（API 原语）→ product（官方命令装配）                                |
| `preloader_rw/relay/`    | 常驻壳：设备生命周期、意图分发、JSON 前端                                                                           |
| `preloader_rw/client/`   | PySide6 GUI（经 JSON 与壳通信）                                                                          |
| `preloader_rw/backends/` | 传输后端：libusb（自带 DLL 与 LGPL-2.1 文本）/ winusb（含 `driver/` 驱动安装包）/ fastboot（Google 二进制与 Apache-2.0 文本） |
| `preloader_rw/payloads/` | 载荷 = 工作流（默认 `route.py`：官方 DA1→DA2→签名流程；`minimal.py` 为可替换性示例；`kernel_api_t.h` 为原生载荷 ABI）           |
| `preloader_rw/profiles/` | 命令表：内核认识哪些命令、帧怎么拼、探针发哪条（默认 `mtk_official.json`；`example_minimal.json` 为格式样例）                       |
| `scripts/`               | 启动脚本、示例配置与命令表自检（`启动程序.bat` / `launch_gui.bat` / `_find_python.py` / `config.example.bat` / `check_table.py`）  |
| `data/`                  | 参考分区表（实测取自 vivo 机型；刷写页初始显示，设备读回后替换）                                                       |

## 免责声明

本工具仅供**你自己拥有的设备**或**获得明确授权**的研究与维修用途。刷写分区  
可能导致设备变砖、数据丢失或失去保修；作者与贡献者不承担任何责任。本项目与  
MediaTek 没有任何关联，DA1/DA2 镜像的获取与使用由使用者自行负责其合法性。  
使用即表示你理解并接受上述风险。

## 第三方组件

- `preloader_rw/backends/fastboot/`：Google Android platform-tools 的 fastboot 客户端，  
  Apache-2.0（全文 `preloader_rw/backends/fastboot/LICENSE-APACHE-2.0.txt`）。
- `preloader_rw/backends/libusb/libusb-1.0.dll`：libusb，LGPL-2.1（全文 `preloader_rw/backends/libusb/LICENSE-LGPL-2.1.txt`）。

## 许可证

本工程自身代码为 MIT（见 [LICENSE](LICENSE)）；上述第三方组件各随其  
许可证，与 MIT 互不干扰。
