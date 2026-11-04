# 固件刷写控制台

## 运行

```powershell
pip install -r requirements.txt
python -m preloader_rw.client
```

## 参数

```powershell
python -m preloader_rw.client --page 1     # 0 资源 1 刷写 2 日志 3 运行记录
python -m preloader_rw.client --shot out.png --page 1
python -m preloader_rw.client --shot out.png --page 1 --shell
```

## 页面

| 页 | 内容 |
|---|---|
| 资源 | 分析对象（DA1 / 2nd-DA / 载荷，原位选择文件）、一键合并物理区 |
| 刷写 | 链路轨、设备枚举状态行、分区表、镜像映射、读取 / 刷写 / 擦除、逐条进度 |
| 日志 | 级别过滤、关键字过滤、导出 |
| 运行记录 | 轮次、判值、产物、结束 |

## 认证

设备识别后进入认证：连接 → 资源 → 签名 → 认证 → 会话。设备开口要签名应答时
弹窗实时输入（选文件或粘贴文本），不预选、不预置；取消窗口即中止。

## 刷写页

- 行来源是壳的 `info` 快照（`partitions` + `images`），GUI 不选配置。
- 勾选 → `开始刷写` 按勾选顺序**逐条串行**提交 `write`，一条结算再发下一条（内核 BUSY 会拒绝并行）。
- `开始读取` 走同一队列，改为 `read`，每个分区一个 `<out_dir>/<分区>.img`。
- 每行 `擦除` 先弹确认再发 `erase`。
- `取消` 只清空后续队列，正在执行的那一条会跑完。

## 外观约定

界面是黑白的：整个表里只有一个色相，只用于失败（`theme.ALERT`）。其余全部靠明度分层：

```
底 → 面     VOID < SURF < CARD < CARD_2
弱 → 强     GHOST < DIM < MUTE < INK
状态        空闲 DIM · 进行中 MUTE · 就绪 INK · 失败 ALERT
```

要加一个新色相之前先回答一句：这条信息能不能只靠明度说清楚。能，就不加。
四种状态正好对应四阶明度——这不是巧合，是这张表的上限。

余下三条同样硬性：

- **不要阴影、不要渐变。** 层级由明度和 1px 线表达，不由光照表达。
- **不要圆角。** 读数需要硬边界；`R_SM` 是 0。
- **数值一律等宽**（`Cascadia Mono`）。读数是这张表里唯一的主角，其余都在说明它。

尺寸统一在 `theme.py`：侧栏 172、顶栏 40、底栏 24、导航行 30、表格行 24、
卡片内边距 12、块间距 6。新控件先套这几个数，再谈别的。

一页只留一个实心按钮：主操作反白，次操作描边。

## 结构

```
preloader_rw/client/
  app.py       外壳：侧栏 / 顶栏 / 底栏 / 页面栈
  theme.py     调色、字号、尺寸、QSS
  icons.py     线性图标（24 网格手绘）
  widgets.py   自绘控件：卡片、读数条、徽章、导航行
  pages.py     四个页面
  bridge.py    与壳进程的唯一通信边界
  state.py     壳推送的状态镜像
  protocol.py  报文编解码
```
