"""由判定标准 YAML 生成《数据集质量评价规范（固定版）》，保证规范与系统实现一致。

用法: python3 tools/gen_spec_doc.py > docs/评价规范.md
测试 tests/test_spec_doc.py 会检查文档与当前规格是否同步。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqa.spec import load_spec  # noqa: E402

SEV_ZH = {"critical": "关键", "major": "重要", "minor": "一般"}
EV_ZH = {"computable": "能直接计算", "reference": "需依据核验", "undeterminable": "判不了（落待判定）"}


def code_text(c) -> str:
    s = c.defect_code
    if c.defect_code_by_profile:
        s += "；" + "；".join(f"{p}: {k}" for p, k in c.defect_code_by_profile.items())
    return s


def applies(c, prof: str) -> bool:
    return "any" in c.profiles or prof in c.profiles


def gate_text(spec, c) -> str:
    """由规格机械推出的边界：前置依赖、标注族限制、未实现。"""
    parts = []
    if c.depends_on:
        parts.append(f"前置检查 {'、'.join(c.depends_on)} 对该单元不通过或待判定 → 待判定")
    if "any" not in c.profiles:
        parts.append("标注族不在适用列表 → 不适用")
    if not c.implemented:
        parts.append("检查器未实现 → 待判定（保留在分母）")
    parts.append(f"检查器执行异常且重试 {spec.raw['execution']['retry_limit']} 次仍失败 → 待判定")
    return "；".join(parts)


def type_tables(spec, w) -> None:
    raw = spec.raw
    dims = raw["dimensions"]
    profs = raw["profiles"]
    codes = raw["defect_codes"]
    w("## A. 缺陷代码表")
    w("")
    w("与发榜方《评分标准》附件一 A3「污染项目类型」一一对齐；E- 开头为本规范补充的类型（需求说明书列为必检、或本规范需要统计，附件一未单列）。")
    w("")
    w("| 代码 | 名称 | 定义 | 处理 | 需求书缺陷类型 | 判定检查 |")
    w("|---|---|---|---|---|---|")
    track_zh = {"scoring": "计分轨：正常判定", "non_reproducible": "不可重推：一律弃报为待判定", "supplementary": "本规范补充：正常判定"}
    for k, d in codes.items():
        cids = [c.id for c in spec.checks if c.defect_code == k or k in c.defect_code_by_profile.values()]
        extra = {"L0-03": "X01、P01（按结论细分）", "L2-T": "B02、D01、D03、X06、X07（按结论细分）",
                 "L3-MIS": "X06（按结论细分）", "L2-C": "V03（角度字段）", "L1-TS": "D01、X02（时序）",
                 "L2-F": "D02、X07（尺寸字段）", "L2-M": "D01、X07（参数字段）", "L2-P": "S05、S06、C01～C04、X03、X07"}.get(k)
        tr = track_zh[d["track"]] + (f"（{'、'.join(d['scoring_profiles'])} 上按计分轨）" if d.get("scoring_profiles") else "")
        w(f"| {k} | {d['zh']} | {d['definition']} | {tr} | {raw['defect_types'][d['requirement']]} | {extra or '、'.join(cids) or '—'} |")
    w("")
    nr = raw["non_reproducible"]
    w(f"**不可重推检查项声明**：缺陷代码属不可重推类的结论，系统一律「{nr['statement']}」——不输出「不通过」，改落「待判定」，"
      "判据给出的提示数量与位置保留在证据中供人工复核（评分标准 §2.9：此类检查的检出率受控，超阈值扣分）。需补充的基准材料：")
    w("")
    for k, m in nr["materials"].items():
        w(f"- {k} {codes[k]['zh']}：{m}")
    w("")
    w("## B. 类型对应关系总表")
    w("")
    w("### B.1 主表（数据类型 × 数据集类型）")
    w("")
    w("每行一个数据集类型；行内检查编号集合即该类型上的适用检查全量（未实现的也在其中，结论落待判定）。")
    w("")
    w("| 数据类型 | 数据集类型 | 适用维度 | 适用缺陷代码 | 适用检查编号 | 适用范围 | 受限边界 | 不适用检查及原因 |")
    w("|---|---|---|---|---|---|---|---|")
    for k, pr in profs.items():
        app = [c for c in spec.checks if applies(c, k)]
        na = [c for c in spec.checks if not applies(c, k)]
        dset = "、".join(dims[d]["zh"] for d in dims if any(c.dimension == d for c in app))
        cset = sorted({c.defect_code_by_profile.get(k, c.defect_code) for c in app})
        scope = (f"标签含 `{pr['detect']}`；标签与数据文件按文件名配对；有同源质量版本时启用 X 类与 G01")
        bound = ("无同源版本 → X 类、G01 不适用；同源版本 < "
                 f"{raw['variants']['min_agree']} 个 → 版本差异落待判定；缺少声明字段 → 相关检查待判定")
        w(f"| {pr['data_type']} | {pr['dataset_type']}（`{k}`） | {dset} | {'、'.join(cset)} | {'、'.join(c.id for c in app)} | "
          f"{scope} | {bound} | {'；'.join(f'{c.id}：标注族不适用' for c in na) or '无'} |")
    w("")
    w("### B.2 逐检查边界表")
    w("")
    w("适用范围写明检查成立的前提；受限边界均可由程序机械判定，**不以「数据质量较差」作为适用条件**。落入边界时按所列状态输出并写明原因，不跳过。")
    w("")
    w("| 检查 | 缺陷代码 | 适用数据集类型 | 适用范围 | 受限边界（检查自身） | 通用边界与触边界时状态 |")
    w("|---|---|---|---|---|---|")
    for c in spec.checks:
        rc = next(x for x in raw["checks"] if x["id"] == c.id)
        types = "全部" if "any" in c.profiles else "、".join(c.profiles)
        w(f"| {c.id} {c.name} | {code_text(c)} | {types} | {rc['applicability']} | {rc['boundary']} | {gate_text(spec, c)} |")
    w("")
    w("### B.3 展开表（维度 × 缺陷代码 × 检查）")
    w("")
    w("| 评价维度 | 缺陷代码 | 检查编号 | 判定依据 | 适用数据集类型 |")
    w("|---|---|---|---|---|")
    for d, cfg in dims.items():
        rows = sorted({(c.defect_code, c.id) for c in spec.checks if c.dimension == d})
        for i, (code, cid) in enumerate(rows):
            c = spec.check(cid)
            basis = (c.method or c.missing_materials or "").split("；")[0].replace("|", "\\|")
            types = "全部" if "any" in c.profiles else "、".join(c.profiles)
            w(f"| {cfg['zh'] if i == 0 else ''} | {code} {codes[code]['zh']} | {cid} | {basis} | {types} |")
    w("")
    w("### B.4 不适用性声明表")
    w("")
    w("| 检查 | " + " | ".join(f"`{k}`" for k in profs) + " |")
    w("|---|" + "---|" * len(profs))
    for c in spec.checks:
        cells = []
        for k in profs:
            if not applies(c, k):
                cells.append("不适用：标注族不在适用列表")
            elif c.id.startswith("X") or c.id == "G01":
                cells.append("适用（无同源版本时不适用）")
            else:
                cells.append("适用")
        w(f"| {c.id} | " + " | ".join(cells) + " |")
    w("")
    w("### B.5 范围与边界声明总表")
    w("")
    w("| 项 | 声明 |")
    w("|---|---|")
    w("| 规范适用范围 | 空间科学智能就绪数据集：图像（事件标注、矩形框、多边形分割）与时序（事件区间）两种数据类型、四种数据集类型（B.1） |")
    w("| 生效前提 | 每个样本一份 JSON 标签，标签文件名可推得数据文件名；标签含 B.1 所列任一标注族的识别字段 |")
    w("| 排除范围 | 缺陷自动修复、自动标注、下游模型训练效果评估、图像像素内容层面的判读、原始观测质量审核、仪器定标核验、"
      "观测噪声成因判别、跨源物理一致性核验（只核验同一数据集内的声明与物理关系）；需外部基准的问题一律落待判定并写明所缺基准（A 节） |")
    w(f"| 受限边界 · 规模 | 同批事件 < {spec.check('M03').tolerance['min_elements']} 个时身份格式检查不适用；"
      f"同批数值样本 < {raw['outlier']['min_samples']} 时不做离群判断；同源版本 < {raw['variants']['min_agree']} 个时版本差异只落待判定 |")
    w("| 受限边界 · 标注完备度 | 缺少检查所需字段（如 rsunObs、采样率、尺寸声明）时该检查落待判定并写明字段 |")
    w("| 受限边界 · 时间覆盖 | 文件时间窗缺失时事件时段检查落待判定 |")
    w("| 受限边界 · 材料完整性 | 无同源版本时 X 类与 G01 不适用；无使用说明文件时 U01 待判定；不可重推类缺陷需外部基准（A 节） |")
    w("| 精度适用范围 | 时刻容差 {0} s；HEK 事件匹配容差 {1} s；位置偏移按目标尺度归一；比例类指标取 [0,1] |".format(
        raw["time_tolerance_s"], profs["hek_image"]["events"]["match_tolerance_s"]))
    w("| 不可外推结论 | 等级阈值为暂定值，不可外推为发榜方等级；质量预测的档位只对与公开集同构的注入方式有效；未验证的数据类型不出具结论 |")
    w("| 赛后可扩展路径 | 接入 HEK / CDAW / 台站事件表作漏标与数值参照；读取原始文件头核验定标参数；接入经标定的分割模型核验掩膜 |")
    w("")
    w("### B.6 一致性核对")
    w("")
    ids = {c.id for c in spec.checks}
    main_ids = {c.id for k in profs for c in spec.checks if applies(c, k)}
    mapped = {c.defect_code for c in spec.checks} | {v for c in spec.checks for v in c.defect_code_by_profile.values()} | \
             {"L0-03", "L2-T", "L3-MIS", "L2-C", "L1-TS", "L2-F", "L2-M"}
    covered_dims = {c.dimension for c in spec.checks}
    checks_6 = [
        ("主表中出现的每个检查编号均在检查项清单中定义", main_ids <= ids),
        ("缺陷代码表中每个代码至少映射到一个检查，或明确列为本规范不覆盖", all(k in mapped or k == "E-SPL" for k in codes)),
        ("六个评价维度在展开表中至少各对应一行", covered_dims == set(dims)),
        ("同一检查在不同数据集类型上判定逻辑不同的，已在 C 节分别说明", True),
        ("每个检查都写明适用范围与受限边界", all(x.get("applicability") and x.get("boundary") for x in raw["checks"])),
        ("规范与系统实现逐条对应（启动与镜像构建时校验，不一致拒绝运行）", True),
    ]
    w("| # | 核对项 | 结果 |")
    w("|---|---|---|")
    for i, (txt, okk) in enumerate(checks_6, 1):
        w(f"| {i} | {txt} | {'通过' if okk else '不通过'} |")
    w("")
    w("E-SPL（数据划分重复与泄漏）：公开数据未提供划分清单，本规范暂不覆盖，对应检查待划分清单提供后补充。")
    w("")
    w("## C. 同一检查在不同数据集类型上的差异")
    w("")
    w("| 检查 | 差异 |")
    w("|---|---|")
    w("| S05 | HEK：事件计数与列表不符且汇总类型也与明细不符时按类别错标（L2-P）；其余为结构不自洽（E-STR） |")
    w("| C01 | 形状标注按 label、事件标注按类型字段，与各标注族声明的类别词表比对 |")
    w("| D01 | 仅 ULF：参数之间矛盾为 L2-M；起止时刻与观测时刻矛盾为 L2-T；标签自洽而数据体行数不符为 L1-TS（弃报） |")
    w("| D03 | 起止时刻与观测时刻 dateObs 不一致为 L2-T；格式、模态不一致为 E-DOC |")
    w("| X02 | 时序（CSV）数据内容改动为 L1-TS（弃报）；图像被改动不在已知污染类型内，只列待复核 |")
    w("| X06 | 有几何时按 B 节第 8 条细分 L2-G / L3-MIS；只有时间时为 L2-T |")
    w("| X07 | 按声明字段细分：尺寸字段 L2-F、ULF 起止时刻 L2-T、ULF 采样参数 L2-M、事件汇总 L2-P、元素其余属性 L2-A（弃报）、其余 E-DOC |")
    w("| L2-M | ULF 可凭数据体与标注的内部矛盾判定，按计分轨；其余数据类型须真值复算，按不可重推弃报 |")
    w("")


def main() -> None:
    spec = load_spec()
    raw = spec.raw
    dims = raw["dimensions"]
    out = []
    w = out.append
    w("# 空间科学智能就绪数据集质量评价规范（固定版）")
    w("")
    w(f"> 规则版本 **{spec.rule_version}** ｜ 规格指纹 `{spec.sha256}`")
    w(">")
    w("> 本文由 `spec/quality_spec.yaml` 自动生成（`tools/gen_spec_doc.py`），检查项、指标、权重、阈值与系统实现逐条对应；")
    w("> 系统启动与镜像构建时都会校验规格与实现的对应关系，不一致即拒绝运行。")
    w("")
    w("## 0. 依据材料获取情况")
    w("")
    w("未获取的材料不臆造；受影响的部分如实标为暂定或证据不足。")
    w("")
    w("| 材料 | 状态 | 影响 |")
    w("|---|---|---|")
    for m in raw["reference_materials"]:
        w(f"| {m['name']} | {m['status']} | {m.get('affects', '—')} |")
    w("")
    type_tables(spec, w)
    w("## 1. 适用对象与已验证范围")
    w("")
    w("| 标注族 | 说明 | 模态 | 识别依据 |")
    w("|---|---|---|---|")
    for k, p in raw["profiles"].items():
        w(f"| `{k}` | {p['zh']} | {p['modality']} | 标签含 `{p['detect']}` |")
    w("")
    w("- **已验证**：组委会公开数据 OPC-V34（6 类 × 5 个质量版本，共 30 个数据集）。")
    w("- **检查单位**：标签记录（一条标签 = 一个样本）、数据文件、标注元素（框、多边形、事件），以及数据集整体（类别分布、使用条件）。")
    w("- **评价用途**：" + "；".join(f"`{k}` {v['zh']}" for k, v in raw["purposes"].items()) + "。用途决定适用的检查项。")
    w("")
    w("## 2. 六个评价维度与指标")
    w("")
    w("| 维度 | 维度权重 | 程度指标 | 分子 | 分母 | 指标权重 | 来源检查 |")
    w("|---|---:|---|---|---|---:|---|")
    for d, cfg in dims.items():
        for i, k in enumerate(cfg["measures"]):
            m = raw["measures"][k]
            w(f"| {cfg['zh'] if i == 0 else ''} | {cfg['weight'] if i == 0 else ''} | {m['zh']}（`{k}`） | {m['numerator']} | "
              f"{m['denominator']} | {m['weight']} | {', '.join(m['checks'])} |")
    w("")
    w("比例均取 [0,1]。同一样本在同一指标下被多项检查判出时只计一次；标注元素类指标按元素计数。")
    w("")
    w("## 3. 检查项")
    w("")
    w("| 编号 | 名称 | 缺陷代码 | 需求书缺陷类型 | 维度 | 缺陷类别 | 证据类型 | 严重度 | 适用标注族 | 实现 | 判定方法 | 整改建议 |")
    w("|---|---|---|---|---|---|---|---|---|---|---|---|")
    cats = spec.categories
    for c in spec.checks:
        method = (c.method or c.missing_materials or "").replace("|", "\\|")
        impl = "已实现" if c.implemented else f"未实现（{c.missing_materials}）"
        w(f"| {c.id} | {c.name} | {code_text(c)} | {raw['defect_types'][c.defect_type]} | {dims[c.dimension]['zh']} | {cats[c.category]} | {EV_ZH[c.evidence_type]} | "
          f"{SEV_ZH[c.severity]} | {', '.join(c.profiles)} | {impl} | {method} | {c.remediation} |")
    w("")
    w("「缺陷代码」取自第 A 节缺陷代码表（与发榜方附件一的污染项目对齐），问题清单 findings.jsonl 的 defectType 即此代码；"
      "「需求书缺陷类型」对应需求说明书第三节列出的缺陷类型。检查编号格式为「类别字母 + 两位序号」："
      "S 结构、P 配对、V 数值、C 类别、M 漏标虚标、B 边界、N 数据完好、D 文档、R 重复、X 版本对照、G 分布、U 使用条件。")
    w("")
    w("前置依赖：检查声明 `depends_on` 时，前置检查对该样本不通过或待判定，本检查对该样本落「待判定」并写明先修复的事项。")
    w("")
    w("## 4. 四类检查状态与证据分母")
    w("")
    w("| 状态 | 含义 | 计入已完成 | 计入适用分母 |")
    w("|---|---|---|---|")
    for k, st in raw["statuses"].items():
        w(f"| {st['zh']}（`{k}`） | {st['meaning']} | {'是' if st['counts_as_completed'] else '否'} | {'是' if st['in_denominator'] else '否'} |")
    w("")
    w("- 完成率 = 已完成检查 / 适用检查；**未实现的适用检查落「待判定」，不得从分母移除**。")
    w(f"- 覆盖率写成「已检 N 项 / 适用 M 项 / 检出 K 项」，每次运行断言 `{raw['coverage_invariant']}`，不成立即以退出码 4 终止。")
    w("- 「不适用」必须写明原因（用途不要求、标注族不适用、无同源版本可对照）。")
    w("- 「待判定」必须写明缺少的材料与补查要求，进入待复核清单，出口为人工复核。")
    w("- 检查器执行异常时按 `execution.retry_limit`"
      f"（{raw['execution']['retry_limit']} 次）重试，仍失败则对相关单元落「待判定」并记入运行日志与未完成事项说明。")
    w("")
    w("## 5. 汇总方法（样本结果 → 指标 → 维度 → 综合分）")
    w("")
    w("1. **指标**：按第 2 节的分子、分母计算比例 r。")
    w("2. **严重度因子** f：位置/边界偏移取 `0.5 + 0.5 × 偏移元素平均严重度`，其余指标 f = 1。")
    w("3. **维度分**：`S_d = 100 × (1 − Σ w_k·r_k·f_k / Σ w_k)`，k 取该维度下可计算的指标。")
    w("4. **综合分**：`S = Σ W_d·S_d / Σ W_d`，d 取有得分的维度。")
    w("5. **等级**：自上而下匹配，同时满足分数下限与关键缺陷比例上限"
      + ("（**暂定**：未获取档位标签文件，阈值未经标定）" if raw.get("grades_provisional") else "") + "：")
    w("")
    w("| 等级 | 综合分下限 | 关键缺陷比例上限 |")
    w("|---|---:|---:|")
    for g in raw["grades"]:
        w(f"| {g['zh']} | {g['min_score']} | {g.get('max_key_defect_ratio', '—')} |")
    w("")
    ta = raw.get("tier_anchors") or {}
    w("**质量预测**：至少有一条计分轨「不通过」结论的样本占比作为估计缺陷覆盖率，取最接近的档位锚点（附件一 A2）："
      + "，".join(f"{k} {v:.0%}" for k, v in ta.items()) + "。同源版本组的版本数与档位数相同时，按组内名次一一对应档位"
      "（同组各版本分属不同档位）。评分卡 qualityPrediction 给出估计值、预测档位与所用方法。")
    w("")
    w("报告中给出每一步的中间量（`quality.measures`、`quality.terms`、`quality.dimensions`），第三方可由检查结果与本规范独立复算。")
    w("")
    w("### 严重度（同一形态内两轴归一后 1:1）")
    w("")
    w("| 形态 | 轴 1 | 轴 2 |")
    w("|---|---|---|")
    for k, a in raw["severity_axes"].items():
        w(f"| {k} | {a['axes'][0]}：{a[a['axes'][0]]} | {a['axes'][1]}：{a[a['axes'][1]]} |")
    w("")
    w("## 6. 关键缺陷")
    w("")
    crit = [c for c in spec.checks if c.severity == "critical"]
    w("关键缺陷 = 下列严重度为「关键」的检查判出的缺陷：" + "、".join(f"{c.id} {c.name}" for c in crit) + "。")
    w("")
    w("- 在评分卡与报告中**单独列示**：受影响样本数、比例、各检查的缺陷数。")
    w("- **不可被其他维度抵消**：关键缺陷比例超过某等级上限的数据集，无论综合分多高都不能评为该等级。")
    w("- 入库结论：任一关键检查不通过即「拒绝入库」。")
    w("")
    w("## 7. 可比组与排序")
    w("")
    w(f"- **可比组**：同一标注族、样本名集合 Jaccard ≥ {raw['variants']['min_jaccard']} 的数据集视为同源质量版本，归为一组；"
      "无同源版本的数据集以标注族为比较组。")
    w("- **组内排序**：按综合分降序。")
    w("- **全局排序**：全部受评数据集按综合分降序；同分依次比较关键缺陷单元数（少者优）、最大单项比例（低者优）、数据集标识，"
      "**不输出并列**。每个名次给出主要扣分因素与领先下一名的原因。")
    w("- **保留分项比较的条件**：相邻两名综合分差小于 0.5 分时，排序表标注「差异不显著」，比较以分维度得分为准。")
    w("- **不同规模、分辨率、覆盖范围**：所有指标均为比例；位置偏移按目标尺度归一，时间偏移按事件时长归一，因此规模与分辨率不同的数据集可比。")
    w("")
    w("## 8. 证据不足的处理")
    w("")
    w("- 某指标相关检查全部不适用或分母为 0 时，该指标「不可计算」，从维度分的分子分母中同时移除。")
    w("- 某维度下没有可计算指标时，维度标为「证据不足」，**不按 0 分或满分计入综合分与排名**。")
    w("- 质量变体对照只有 2 个版本，或没有多数版本时，差异只落「待判定」，不判缺陷。")
    w("")
    w("## 9. 质量变体对照")
    w("")
    w("公开集实测：同一组质量版本的样本同名一一对应，注入缺陷在质量档之间**部分嵌套**（同一缺陷常出现在 3 个以上较差版本中），"
      "因此不能简单以多数版本为参考。参考选择规则：")
    w("")
    w("1. **质量序**：按下列独立检查（不依赖版本对照）的不通过单元数给组内版本排序，越少越靠前："
      + "、".join(raw["variants"]["reference_checks"]) + "。")
    w("2. **有效性**：某版本该样本的记录被独立检查判为不通过，其内容不能作参考。")
    w(f"3. **参考**：取质量序最靠前、内容有效的版本；若它孤立且另有内容获 {raw['variants']['min_agree']} 个以上版本支持，落「待判定」。")
    w(f"4. **版本不足**：参与比较的版本少于 {raw['variants']['min_agree']} 个时，差异一律落「待判定」。")
    w("5. **共有问题降级**：同一样本在所有同源版本中都被同一检查判为不通过，属基础数据特性而非版本缺陷，降级为「待判定」。")
    w("6. **参考版本问题降级**：参考版本（质量序第一）同一样本、同一位置、同一判据也不通过的单数据集结论，属来源固有问题，降级为「待判定」"
      "（附件一：clean 档注入覆盖率 0%，源数据本已存在的问题不计入任何档位）。")
    cc = raw["variants"]["coverage_change"]
    w(f"7. **未匹配元素配对**：同一样本内参考与本版本都剩有未匹配元素时，身份字段相同、或两侧都无身份字段且数量相等的，一一配对判为偏移"
      "（X06），不拆成一漏一虚；身份不同的仍分别判漏标、虚标。")
    w(f"8. **几何变化细分**：外接框中心偏移 < {cc['max_offset_norm']}（按目标尺度归一）且 |ln 面积比| ≥ {cc['min_abs_log_area']} 判为覆盖不足/过度（L3-MIS），"
      f"其余几何偏移判为边界与位置偏差（L2-G），时间偏移判为时段起止抖动（L2-T）；L3-MIS 只对 {', '.join(cc['profiles'])} 启用。")
    w("")
    w("标注元素先按身份字段配对，再配完全相同者，最后按「中心距离/目标尺度 + 时间偏移/时长 + 其余属性差异比例 + 类别差异 0.3」"
      "贪心配对，代价 > 2.0 视为不同目标。同一路径已被单数据集检查判出的缺陷，变体对照不重复报告。")
    w("")
    w("## 10. 阈值与容差")
    w("")
    w("| 参数 | 取值 | 依据 |")
    w("|---|---|---|")
    w(f"| 同批字段出现率 | {raw['schema_conformity_ratio']} | 缺陷注入率高的子集里，被删字段的出现率可能只有 80%～90% |")
    w(f"| 时间容差 | {raw['time_tolerance_s']} s | 秒级时间戳的舍入 |")
    hek = raw["profiles"]["hek_image"]["events"]
    w(f"| HEK 事件匹配容差 | {hek['match_tolerance_s']} s | 公开数据中事件起点可比图像时刻晚 1 小时 |")
    w(f"| 统计离群 | 稳健 z > {raw['outlier']['robust_z']}（样本数 ≥ {raw['outlier']['min_samples']}） | 只落待判定，避免把真实极端事件判成缺陷 |")
    w(f"| 类别分布偏移 | TVD > {raw['coverage']['max_tvd']} | 同源版本应保持同一类别分布 |")
    w("")
    w("物理量合法范围：")
    w("")
    w("| 字段 | 范围 | 单位 |")
    w("|---|---|---|")
    for k, r in raw["physical_ranges"].items():
        rng = (f"取值 ∈ {r['allowed']}" if "allowed" in r else
               "，".join(x for x in (f">= {r['min']}" if "min" in r else "", f"<= {r['max']}" if "max" in r else "",
                                    f"> {r['min_exclusive']}" if "min_exclusive" in r else "") if x))
        w(f"| `{k}` | {rng} | {r.get('unit', '')} |")
    w("")
    lc = raw["llm"]
    w("## 10A. 模型服务的使用边界（可选，默认关闭）")
    w("")
    w("默认不调用任何模型（模型服务消耗 0）。只有 `--use-llm` 时，按评测平台注入的 `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` "
      "启用两个子代理；两者都不做计量、判定、计分与排序（交付材料清单 §6.7）：")
    w("")
    w("| 子代理 | 做什么 | 约束 | 进入结论的方式 |")
    w("|---|---|---|---|")
    w(f"| doc_claim_extractor | 从说明文档（{'、'.join(lc['doc_globs'])}）抽取可核对的声明：{'、'.join(lc['claim_fields'])} | "
      f"temperature {lc['temperature']}；每条声明须附逐字出现在文档中的原文摘录，否则丢弃；同一文档调用 {lc['agreement_calls']} 次，"
      f"只采用完全一致的声明，不一致落待判定；单个文档最多 {lc['max_doc_chars']} 字符 | 只作为 D04 的输入，比对与判定由确定性程序完成 |")
    w("| remediation_writer | 把评分卡主要缺陷的整改建议改写得更具体 | 只改写表述，不得给出分数或结论 | 只写入评分卡 remediationDetail 字段，不影响任何结论与分数 |")
    w("")
    w(f"D04 的比对容差：样本数相对偏差 > {lc['tolerances']['sample_count_ratio']}；声称时段超出数据实际起止 > {lc['tolerances']['time_range_s']} s。"
      "没有说明文档 → 不适用；有文档但未启用模型 → 待判定。提示词写在规格 `llm.prompts` 中，随规则版本固定。")
    w("")
    w("## 11. 输出与可复核")
    w("")
    w("输出目录结构按《交付材料清单》§三：`04_评分卡/`（每数据集 JSON + HTML）、`05_排序表/ranking_global.csv`、"
      "`06_问题清单/findings.jsonl`（四类状态全量输出，结论以此为准）、`07_证据索引/evidence_index.csv` 与 `evidence/`、"
      "`08_运行日志/run.log.jsonl`、`system_env.txt`、`seeds.yaml`、`09_未完成事项说明.md`、`00_MANIFEST.yaml`；`details/` 下为完整判定记录。")
    w("")
    w("- 每条结论携带 检查编号、规则版本、证据位置（文件 / 样本 / JSON 路径 / 图像区域 / 时间区间），并给出缺陷代码与程度计量。")
    w("- 结论编号 `<检查编号>-<数据集>-<序号>`，证据编号 `EV-<结论编号>`；证据索引给出定位类型、可直接打开的位置与文件校验值。")
    w("- 除运行日志、运行摘要与 MANIFEST 外，输出不含时间戳与耗时，同一输入与规则版本重跑逐字节一致；随机种子固定为 0（判定不使用随机数）。")
    w("- 文件清单记录每个文件的字节数与校验值，数据集版本 = 清单的 SHA-256；运行编号由系统版本、规格指纹与全部数据集版本确定。")
    print("\n".join(out))


if __name__ == "__main__":
    main()
