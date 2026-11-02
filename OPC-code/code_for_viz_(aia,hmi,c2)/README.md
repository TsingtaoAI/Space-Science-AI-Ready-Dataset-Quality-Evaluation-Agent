# OPC 样例数据可视化（三个独立程序）

针对国家空间科学数据中心 AI-Ready 榜单 OPC 样例数据集的**标注速查与人工抽检**工具。
每个赛段一个独立脚本，互不依赖，可单独拷贝运行。

| 脚本 | 数据集 | 用途 |
| --- | --- | --- |
| `viz_aia_ar.py` | `OPC/AIA_AR_sample` | AIA 活动区（AR）样例 |
| `viz_hmi_ar.py` | `OPC/HMI_AR_sample` | HMI 磁图活动区样例 |
| `viz_cme_c2.py` | `OPC/CME_C2_sample` | LASCO C2 日冕物质抛射样例 |

---

--

## 依赖

```
python >= 3.8    numpy    matplotlib    pillow
```

- 已在本机验证：Python 3.13 + numpy 2.5.4 + matplotlib 3.11.2 + pillow 12.3.0
中文字体：脚本会自动在 `Microsoft YaHei` / `SimHei` / `Noto Sans CJK SC` 中探测。
⚠️ 文本面板显式使用 `family="monospace"`，故**必须同时设置 `font.monospace`**，
只设`font.sans-serif` 会出现 `Glyph ... missing from DejaVu Sans Mono` 警告。

---

## 数据集目录约定

```
<root>/
  data/<name>.png# 灰度原图 4096×4096
  label/<name>.png.json     # 标注（四段结构）
  file_list.txt
  dataset_summary.json
```

标注 JSON 四段：`label_header` / `basic_annotation` / `science_annotation` / `hek_event_annotation`。
AIA/HMI 的标注几何是 `matched_events[].bbox_polygon_pixel`（**已是图像像素坐标**）。

---

## 用法

三个脚本参数完全对齐：

```bash
# 列出样本名
python viz_aia_ar.py --root AIA_AR_sample --list

# 抽检：默认自动挑一个含事件样本
python viz_aia_ar.py --root <AIA_ROOT> --out /tmp/viz_aia.png

# 指定样本（带不带 .png 后缀都行）
python viz_aia_ar.py --root <AIA_ROOT> --file aia_20100605_032600_0094A.png

# 只打印标注摘要，不出图
python viz_aia_ar.py --root <AIA_ROOT> --index 3 --no-plot
```

| 参数 | 含义 |
| --- | --- |
| `--root` | 数据集根目录（含 `data/` 与 `label/`），脚本内置了源端默认值 |
| `--file` | 指定样本名；CME 侧传**相邻帧**也能反查整组 |
| `--index` | 按排序取第 N 个 |
| `--any` | 允许选到无事件样本（默认优先含事件） |
| `--zoom` | 放大子图个数上限（AIA/HMI，默认 3） |
| `--out` | 输出 PNG 路径 |
| `--list` / `--limit` | 列出样本名 / 限制条数 |
| `--no-plot` | 只打印标注摘要 |
| `--signed` | *仅 HMI*：切换为有符号磁图模式（见下） |
| `--orient` | *仅 CME*：`north_up` / `rot180` / `mirror_x` / `mirror_y`，人工覆盖自动判定 |

---

## 三个程序的面板内容

### `viz_aia_ar.py`

- 左：全幅原图（asinh 拉伸）+ `bbox_polygon_pixel` 叠加 + 图例
- 中：径向亮度剖面（自动定标依据，红虚线 = R_disk）
- 右：样本/观测时间/通道/事件统计 + AR 属性表（极性、面积极、无符号磁通、最大场强、通道）
- 下排：最多 3 个事件区域**原始像素级**放大图（不下采样，保证细节）

拉伸方式：`asinh(x·400)/asinh(400)`。94 Å 数据的动态范围极大，线性拉伸会把日面压平。

### `viz_hmi_ar.py`

输出**两张**图：`_png`（主图）与 `_flux.png`（磁斑统计）。

- 左：全幅 |B| 幅值图 + bbox + 日面圆；右下为**磁斑阈值等值线**（`P99` 分位阈值）
- 右：SHARP 特征表（面积、磁通、剪切角、净螺度、alpha、Jz）
- 下排：事件区域放大图
- 副图：每个 bbox 内超阈像元占比 / 非零占比 / P95 灰度

⚠️ **HMI 的 PNG 是无符号 `|B_los|` 幅值图，不是双极磁图**
（日面内中位数 0、约 26% 像元恰为 0）。故默认用单极色表（金→红→黑）+ log1p 压缩。
若按有符号双极着色会凭空造出"极性"，属误导。若确认某批数据确实有符号，用 `--signed`。

### `viz_cme_c2.py`

3×4 面板：

- 第1 行：**中心帧 / 前帧 / 后帧**（asinh 拉伸，遮罩外压暗至 0.06，叠加 CME 角宽扇形 + PA 射线 + occ/视场圆 + 3/6/9 R_s刻度圈）
- 第 2 行：**running difference**（前帧 − 后帧，`RdBu_r` 发散色表）/ **baseline difference**（中心帧 − 前帧）/ 科学遮罩示意（白 = 有效，标注有效占比）
- 第 3 行：三帧各自的径向亮度剖面
- 右列：帧组信息 + 像素定标 + PA 朝向（四朝向实测得分）+ CDAW CME 参数表（PA / width / speed / accel / mass / KE / remarks）

**帧分组规则**（实测全量 35,968 帧 / 12,000 组：11,968 组为 3 帧、32 组仅 2 帧）：

```
hek_event_annotation.image_time        == 中心帧时间
basic_annotation.startTime             == 该帧自身时间
两者相等 → 该帧是中心帧
相邻帧的 hek 标注与中心帧一致
```

---

## 数据侧的三项实测发现

1. **PA 朝向必须实测判定**。脱去 FITS 头后，"北在上 / 旋转 180° / 镜像"成为固有歧义。
   本实现不猜——枚举 4 种朝向，用 running difference 计算
   「扇形内 |差分| 能量 / 全图有效区 |差分| 能量」选优。
   3 个独立 CME 交叉验证**一致选出 `rot180`**（得分比 `north_up` 高 1.4~2.4 倍）。
   可用 `--orient` 人工覆盖。

2. **C2 是方形裁切，外冕被切**。实测`r_fov = 2721 px ≈ 10.6 R_sun`，
   而画幅半边长只有 2048 px（≈8 R_sun）——即四角外冕已缺失。
   故科学遮罩用**图像实测** `r_fov`，不用参考实现的标称 6 R_sun；
   标称值仅用于打印与图上对照。扇形外缘取 `min(实测视场, 方形画幅内切圆)`。

3. **HMI PNG 是无符号 |B| 幅值图**（见上）。

---

## 踩坑记录（留在代码注释里）

- ⚠️ **R_sun→像素换算必须除以 R_sun/px，不能乘**。乘反会把120° 宽的扇形缩成中心一个
  亚像素点（本项目踩过：扇形完全不可见）。见 `rho_to_px()` 的 docstring。
- ⚠️ 叠加几何的 `scale` 与 `imshow` 的坐标系必须一致。若图像按全分辨率显示，
  `scale` 应为 1.0，不能再乘降采样比（否则面板显示为四分之一裁切）。
- ⚠️ `list_labels` 剥离后缀用 `f[:-len(".png.json")]`，不能写 `f[:-8]`
  （会多留一个点，三个脚本都踩过）。
- ⚠️ running difference 用 **P95** 分位而非 P99——CME 常弱于噪声尾部。
- ⚠️ 差分归一化前做**归一化盒式滤波**（积分图实现，零 scipy 依赖）。
- ⚠️ matplotlib 3.11 的 `plt.style.use()` 不再返回 context manager，
  必须用 `plt.style.context()`。