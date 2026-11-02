"""生成智能体构件登记文件 agent/*.yaml（交付材料清单 §六）。

检查编号、工具与检查的归属、技能承载的判定知识均从 spec/quality_spec.yaml 与检查器注册表读取，
保证登记与实现一致；tests/test_agent_registry.py 校验 agent/ 与本脚本输出同步。

用法：python3 tools/gen_agent_registry.py            # 写入 agent/
      python3 tools/gen_agent_registry.py --check    # 只比较，不一致时退出码 1
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sqa import __version__, checks  # noqa: E402,F401  导入即注册检查器
from sqa.registry import REGISTRY  # noqa: E402
from sqa.spec import load_spec  # noqa: E402

HEADER = "# 由 tools/gen_agent_registry.py 依据 spec/quality_spec.yaml 与检查器注册表生成，请勿手改\n"

TOOL_INFO = {
    "structure": ("标注结构检查器", "严格解析标签、识别标注族、校验必填字段与同批结构、事件计数与类型汇总"),
    "pairing": ("文件配对检查器", "标签与数据文件一一对应、引用文件名一致"),
    "values": ("标注数值合法性检查器", "时刻可解析且有序、几何合法、物理量在合法范围内"),
    "categories": ("类别检查器", "类别在声明类别表内、事件类型代码与概念名一致、ULF 事件类型与来源目录一致"),
    "identity": ("虚构标注检查器", "事件身份标识与同批主流格式一致、同一标注内无平移缩放的复制多边形（疑似虚构标注）"),
    "boundary": ("边界检查器", "几何落在图像范围内、事件时段与观测时刻相容"),
    "physics": ("物理一致性检查器", "CME 动能自洽、目标中心位于日面内、目标框面积与像素数相符"),
    "numeric": ("数值完好检查器", "标注数值统计离群、时序数据数值完好、图像文件完好"),
    "docclaims": ("说明文档声明比对器", "把子代理从说明文档抽取的声明与数据实际情况逐项比对（样本数、类别、时段、尺寸、模态）"),
    "docs": ("文档一致性检查器", "时序声明自洽、声明的图像尺寸与文件一致、格式 / 模态 / 观测时刻一致"),
    "duplicates": ("重复样本检查器", "数据文件内容重复"),
    "consistency": ("跨样本一致性检查器", "同一事件在不同样本中的类别与时刻一致、类别与数据集定位相符（无同源版本时运行）"),
    "coverage": ("覆盖与分布检查器", "类别分布与同源版本参考分布一致；使用权限与用途说明"),
    "crossvariant": ("质量版本对照工具", "同源质量版本之间逐样本、逐元素比较，判出缺失、改动、错标、漏标、虚标、偏移"),
}

SKILLS = [
    ("defect_taxonomy", "缺陷代码与计分轨划分", "全部数据类型", ["defect_codes", "field_defect_codes"],
     "缺陷代码与发榜方附件一的污染项目一一对齐；计分轨 / 不可重推 / 本规范补充三类；声明字段到缺陷代码的映射", None),
    ("hek_event_conventions", "HEK 事件标注约定", "太阳图像 · HEK 事件", ["profiles.hek_image"],
     "事件列表位置、计数与类型汇总字段、类型代码与概念名对照、身份字段、单一时刻事件", ["S05", "S06", "C02", "M03"]),
    ("ulf_conventions", "ULF 时序标注约定", "地磁 ULF 时序", ["profiles.ulf_timeseries", "timeseries"],
     "事件区间字段、类型词表、填充值与连续填充段判定", ["C01", "C04", "N02", "D01"]),
    ("shape_conventions", "极光与等离子体泡形状标注约定", "极光图像 · 矩形；气辉图像 · 分割",
     ["profiles.aurora_shapes", "profiles.epb_segmentation"], "形状列表位置、类别词表、图像尺寸声明字段", ["C01", "V02", "D02"]),
    ("required_fields", "必填字段与结构一致性", "全部数据类型", ["required_fields", "schema_conformity_ratio"],
     "赛题要求的必填字段及类型；同批出现比例达阈值的字段视为应有字段", ["S03", "S04"]),
    ("physical_ranges", "物理量合法范围", "太阳图像、地磁时序", ["physical_ranges"],
     "太阳视半径、日地距离、波长、CME 角度与速度、地磁分量等的合法取值", ["V03", "N02"]),
    ("solar_physics", "太阳物理关系", "太阳图像 · HEK 事件", ["physics"],
     "CME 动能 = ½·质量·速度² 的容差；日面边缘余量与像素尺度；目标框面积与像素数之比的合理范围", ["V04", "B03", "B04"]),
    ("outlier_rule", "数值离群判据", "太阳图像、地磁时序", ["outlier"], "稳健 z 分数阈值与最少样本数", ["N01"]),
    ("time_tolerance", "时刻容差", "全部带时刻的数据", ["time_tolerance_s", "profiles.hek_image.events.match_tolerance_s"],
     "声明时刻之间、事件时段与观测时刻之间的容差", ["B02", "D03", "V01"]),
    ("variant_comparison", "质量版本对照判据", "有同源质量版本的数据集", ["variants", "severity_axes"],
     "同源版本分组、参考版本选择、元素匹配代价、未匹配元素配对、几何变化细分、严重度两轴", None),
    ("coverage_tolerance", "分布偏移容差", "有同源质量版本的数据集", ["coverage"], "类别直方图总变差距离容差", ["G01"]),
    ("usage_documents", "使用条件文件识别", "全部数据类型", ["usage"], "识别使用权限、许可与用途说明文件的文件名模式", ["U01"]),
    ("document_claims", "说明文档声明的比对口径", "全部数据类型（有说明文档时）", ["llm.claim_fields", "llm.tolerances", "llm.doc_globs"],
     "可核对的声明类型、样本数与时段的容差、说明文档的识别模式", ["D04"]),
    ("model_prompts", "子代理提示词", "全部数据类型（--use-llm 时）", ["llm.prompts"],
     "doc_claim_extractor 与 remediation_writer 的提示词：只抽取明确写出的声明并附原文、只改写整改表述不下结论", ["D04"]),
    ("cross_sample_grouping", "同一事件的跨样本分组", "太阳图像 · HEK 事件", ["cross_sample"],
     "同一 HEK 事件按身份标识分组、LASCO C2 帧按 image_time 成组（发榜方参考代码）；比较的类别与时刻字段、最少出现次数", ["C05", "B05"]),
    ("dataset_positioning", "数据集定位判据", "太阳图像 · HEK 事件", ["positioning"],
     "数据集定位取占严格多数的类别；参与统计的类别字段、最少类别标注数、多数占比下限", ["C06"]),
    ("scoring_rules", "汇总与评分规则", "全部数据类型", ["measures", "dimensions", "scoring", "grades", "tier_anchors"],
     "缺陷程度指标的分子分母、维度与权重、综合分公式、等级阈值、档位锚点", None),
]


def tools_yaml(spec) -> dict:
    by_mod = defaultdict(list)
    for cid, ch in REGISTRY.items():
        by_mod[ch.fn.__module__.rsplit(".", 1)[-1]].append(cid)
    order = {c.id: i for i, c in enumerate(spec.checks)}
    limit = spec.raw["execution"]["retry_limit"]
    tools = []
    for mod in sorted(by_mod, key=lambda m: min(order[c] for c in by_mod[m])):
        name, purpose = TOOL_INFO[mod]
        cids = sorted(by_mod[mod], key=order.get)
        scopes = sorted({REGISTRY[c].scope for c in cids})
        tools.append({
            "name": f"sqa.checks.{mod}", "title": name, "version": __version__,
            "entry": f"python -m sqa run（镜像 /app/src/sqa/checks/{mod}.py，由编排层按检查编号调用）",
            "purpose": purpose,
            "inputSchema": {"dataset": "DatasetContext：数据集根目录、标签记录、数据文件清单、已解析标签与前置检查结论",
                            "record": "单条标签记录（scope=record 时）" if "record" in scopes else None,
                            "group": "同源质量版本组的比较结果（scope=group 时）" if "group" in scopes else None},
            "outputSchema": "每个单元一条结论：unit、status（通过/不通过/待判定/不适用）、findings[location, message, evidence]",
            "timeout": "不单设；整次运行受评测端单数据集 3 小时上限约束（公开集全量实测约 12 分钟）",
            "retryLimit": limit,
            "failureModes": ["检查器抛出异常：按 retryLimit 重试，仍失败则该单元落待判定并记运行日志（不记为不适用）",
                             "前置检查未通过：不执行，落待判定并写明先修复的检查"],
            "checkIds": cids, "scope": scopes, "deterministic": True,
            "resources": {"cpu": "单进程", "accelerator": "不需要", "network": "不需要"},
        })
    infra = [
        ("sqa.dataset", "数据集识别与加载", "识别数据集目录、配对标签与数据文件", []),
        ("sqa.materials.inventory", "文件清单", "生成文件清单与数据集版本（校验值）", []),
        ("sqa.report", "汇总与评分", "按规格公式计算缺陷程度、维度分、综合分、等级、全局与组内排序", []),
        ("sqa.deliver", "交付物输出", "按交付材料清单输出评分卡、排序表、问题清单、证据索引、运行日志", []),
    ]
    for name, title, purpose, cids in infra:
        tools.append({"name": name, "title": title, "version": __version__, "entry": "python -m sqa run",
                      "purpose": purpose, "inputSchema": "上一步的结构化结果", "outputSchema": "见 deliver 模块说明",
                      "timeout": "不单设", "retryLimit": 0,
                      "failureModes": ["覆盖率不变量被破坏（K ≤ N ≤ M）时终止运行，退出码 4"],
                      "checkIds": cids, "deterministic": True,
                      "resources": {"cpu": "单进程", "accelerator": "不需要", "network": "不需要"}})
    return {"tools": tools}


def skills_yaml(spec) -> dict:
    out = []
    for name, title, scope, paths, knowledge, cids in SKILLS:
        if cids is None:
            if name == "variant_comparison":
                cids = [c.id for c in spec.checks if c.id.startswith("X")]
            elif name == "defect_taxonomy":
                cids = [c.id for c in spec.checks]
            else:
                cids = []
        out.append({"name": name, "title": title, "version": spec.rule_version, "appliesTo": scope,
                    "knowledge": knowledge, "specPaths": [f"spec/quality_spec.yaml#{p}" for p in paths],
                    "ruleVersion": spec.rule_version,
                    "entry": "显式调用：检查器按规格路径读取，无执行体", "checkIds": cids})
    return {"skills": out}


def rules_yaml(spec) -> dict:
    lim = spec.raw["execution"]["retry_limit"]
    all_ids = [c.id for c in spec.checks]
    nr = [code for code, d in spec.raw["defect_codes"].items() if d["track"] == "non_reproducible"]
    rules = [
        ("R01", "触及受限边界转待判定", "must_flag_pending",
         "前置检查不通过或待判定；标注族识别不出；判定所需字段或材料缺失；同源版本少于 min_agree",
         "该单元落「待判定」并写明缺少的材料或先修复的检查，不得静默跳过", "记录到问题清单与运行日志", all_ids),
        ("R02", "证据位置无法确定不得输出", "forbidden_output",
         "缺陷结论没有可回查的文件位置", "禁止输出该条「不通过」，改落「待判定」；只报类别不报位置的结论一律不输出",
         "降级并记运行日志", all_ids),
        ("R03", "工具失败按上限重试", "must_retry",
         "检查器执行抛出异常", f"最多重试 {lim} 次；仍失败则该单元落「待判定」，不得改判为「不适用」",
         "记「重试」「检查失败」「降级」事件", all_ids),
        ("R04", "未实现检查保留在分母", "must_flag_pending",
         "规格中 implemented: false 的适用检查", "保留在检查完成度分母中，结论落「待判定」并写明所缺材料",
         "启动校验规格与实现逐条对应，不一致拒绝运行（退出码 2）", [c.id for c in spec.checks if not c.implemented]),
        ("R05", "不可重推项弃报", "forbidden_output",
         f"结论的缺陷代码属于不可重推类（{'、'.join(nr)}），且不在该标注族的计分轨例外内",
         f"不输出「不通过」，改落「待判定」并写明「{spec.raw['non_reproducible']['statement']}」与需补充的基准材料",
         "降级并记运行日志「降级」事件", sorted({c.id for c in spec.checks})),
        ("R06", "证据不足的维度不计分", "forbidden_output",
         "维度下没有可计算的程度指标；或指标的相关结论全部为不可重推弃报",
         "该维度记「证据不足」，不按 0 分或满分纳入综合分与排序", "评分卡 uncomputableDimensions 列示", []),
        ("R07", "覆盖率不变量", "must_stop", "检出项 K、已完成 N、适用 M 不满足 K ≤ N ≤ M",
         "终止运行，退出码 4", "终止", []),
        ("R08", "参考版本共有问题降级", "must_flag_pending",
         "同一样本在全部同源版本中都被同一检查判为不通过，或参考版本（质量序第一）同一位置同一判据也不通过",
         "降级为「待判定」，写明属来源固有问题", "记入问题清单（待复核事项）", all_ids),
        ("R09", "排序不得并列", "forbidden_output", "两个数据集综合分相同",
         "依次比较关键缺陷单元数、最大单项比例、数据集标识，保证全局排序 1..n 连续", "—", []),
        ("R10", "运行配额", "quota", "每次运行",
         "并行进程数 ≤ 容器可用 CPU 数；除评测平台注入的模型服务外不访问网络；默认不调用模型服务（消耗 0），"
         "--use-llm 时只由两个子代理调用；只写输出目录", "超出即不启动对应进程", []),
        ("R11", "模型配置缺失即停", "must_stop", "启用 --use-llm 但 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL 缺失",
         "终止运行，退出码 5，不使用默认值", "终止", []),
        ("R12", "模型输出不得直接成为结论", "forbidden_output",
         "子代理返回结果", "抽取的声明须附逐字出现在文档中的原文摘录，否则丢弃；"
         f"同一文档抽取 {spec.raw['llm']['agreement_calls']} 次只采用一致的声明，不一致落待判定；模型输出不进入计量、计分与排序",
         "丢弃或降级为待判定，记运行日志", ["D04"]),
    ]
    return {"rules": [{"id": i, "name": n, "version": spec.rule_version, "type": t, "trigger": tr,
                       "constraint": cons, "onViolation": viol, "checkIds": cids}
                      for i, n, t, tr, cons, viol, cids in rules]}


def memory_yaml(spec) -> dict:
    return {"memory": [
        {"name": "dataset_context", "scope": "单次运行内 · 跨检查步骤（同一数据集）",
         "storage": "进程内存（DatasetContext.parsed / outcomes / cache）",
         "writeOn": ["标签解析成功（S01）", "每个检查得出逐单元结论", "数据文件指纹、CSV 扫描、身份格式统计首次计算"],
         "readers": ["编排层：按 depends_on 判断前置检查是否通过", "检查器：复用已解析标签与扫描结果"],
         "consistency": "每个数据集一个上下文，单进程独占写入；结论只追加不改写（降级与弃报生成新结论并记事件）",
         "crossRunPersistence": "无：运行结束即释放，不读取任何历史记忆；重复运行结果逐字节一致"},
        {"name": "variant_group_context", "scope": "单次运行内 · 跨数据集（同一质量版本组）",
         "storage": "进程内存（组比较结果 GroupResult）", "writeOn": ["组内逐样本比较完成"],
         "readers": ["版本对照检查 X01～X08"], "consistency": "每组在一个进程内完成，组与组之间无共享状态",
         "crossRunPersistence": "无"},
    ], "rule": "记忆不是结论的来源：每条结论都能从问题清单回查到原始文件与证据"}


def plugins_yaml(spec) -> dict:
    return {"plugins": [
        {"name": "checker_registry", "version": __version__, "extensionPoint": "工具位：@check(编号) 装饰器 + 规格中同编号条目",
         "capability": "新增检查器：在 src/sqa/checks/ 写实现、在规格中声明同一编号，启动校验通过即生效",
         "enabledByDefault": True, "dependsOn": [], "conflictsWith": []},
        {"name": "profile_definitions", "version": spec.rule_version, "extensionPoint": "决策点：标注族识别",
         "capability": "新数据类型：在规格 profiles 中声明识别字段、元素路径、类别词表即可复用全部通用检查",
         "enabledByDefault": True, "dependsOn": ["checker_registry"], "conflictsWith": []},
        {"name": "llm_client", "version": __version__, "extensionPoint": "子代理位：模型服务客户端（OpenAI 兼容）",
         "capability": "--use-llm 时按环境变量连接评测平台提供的模型服务，供 doc_claim_extractor 与 remediation_writer 使用，"
                       "逐次计量消耗；默认关闭",
         "enabledByDefault": False, "dependsOn": [], "conflictsWith": []},
    ], "loading": "全部随镜像交付，运行时不从外部加载任何插件"}


def manifest_yaml(spec) -> dict:
    cfg = spec.raw["variants"]
    return {
        "agent": {"name": "空间科学智能就绪数据集质量评价智能体", "version": __version__, "ruleVersion": spec.rule_version,
                  "paradigm": "pipeline"},
        "modelToolWhitelist": [],
        "modelToolWhitelistNote": "编排为确定性流水线；--use-llm 时两个子代理只接收文本、返回文本，不调用任何工具",
        "decisionPoints": [
            {"id": "DP1", "name": "标注族识别", "trigger": "每条标签解析成功后", "input": "标签 JSON",
             "output": "profile 名或 None", "fallback": "识别不出：该记录相关检查落待判定（R01）"},
            {"id": "DP2", "name": "检查适用性", "trigger": "每个检查执行前", "input": "数据集标注族、用途、检查的 profiles/purposes",
             "output": "适用 / 整项不适用（附原因）", "fallback": "无法确定时逐条执行，不整项排除"},
            {"id": "DP3", "name": "前置依赖", "trigger": "每个单元执行前", "input": "depends_on 中检查在该单元的结论",
             "output": "执行 / 落待判定", "fallback": "R01"},
            {"id": "DP4", "name": "同源版本分组与参考版本", "trigger": "全部单数据集检查完成后",
             "input": "同标注族数据集的样本名集合、独立检查的不通过单元数",
             "output": "版本组、质量序、逐样本参考内容",
             "fallback": f"组内少于 {cfg['min_agree']} 个版本或无有效参考：差异落待判定"},
            {"id": "DP5", "name": "缺陷代码与弃报", "trigger": "输出结论前", "input": "检查编号、标注族、结论证据",
             "output": "缺陷代码；不可重推者改落待判定", "fallback": "R05"},
            {"id": "DP6", "name": "汇总与排序", "trigger": "全部结论确定后", "input": "逐检查结论、规格中的指标与权重",
             "output": "维度分、综合分、等级、质量预测、全局排序", "fallback": "证据不足的维度不计分（R06）；同分不并列（R09）"},
        ],
        "deterministicBoundary": [
            {"step": "数据读取、解析、校验", "model": "不允许", "implementation": "确定性程序"},
            {"step": "数值计量（比例、偏移、统计量）", "model": "不允许", "implementation": "确定性程序"},
            {"step": "适用性判定", "model": "允许但未使用", "implementation": "确定性程序（DP2）"},
            {"step": "缺陷判定与分类", "model": "允许，须给出证据位置",
             "implementation": "确定性程序；--use-llm 时子代理只从说明文档抽取声明（附原文摘录作为证据位置），判定仍由程序比对完成（D04）"},
            {"step": "严重程度判断", "model": "允许但未使用", "implementation": "确定性程序（规格 severity_axes）"},
            {"step": "维度得分与综合排序", "model": "不允许", "implementation": "确定性程序（规格 scoring）"},
        ],
        "orchestration": {"mainLoop": ["发现数据集", "按质量版本组预分组", "组内逐数据集执行单数据集检查（按依赖拓扑序）",
                                       "组内版本对照", "降级共有与参考版本问题", "不可重推项弃报", "生成报告与评分",
                                       "全局排序与交付物输出"],
                          "concurrency": "按质量版本组多进程并行；进程数 = min(组数, 容器可用 CPU 数)，可用 --workers 指定",
                          "state": "进程内存；结果只写输出目录", "context": "每个数据集一个上下文对象，组间不共享"},
        "seeds": {"global": 0, "sampling": None, "note": "全程确定性，不抽样"},
        "runtime": {"base": "python:3.11.9-slim-bookworm", "dependencies": {"PyYAML": yaml.__version__},
                    "processModel": "主进程 + 每个质量版本组一个工作进程", "isolation": "Docker 容器，--network none，输入只读挂载"},
        "guardrails": {"writeWhitelist": ["/output"], "network": "不访问；评测时以 --network none 运行",
                       "process": "只创建本程序的工作进程", "quotas": {"workers": "≤ 可用 CPU 数", "retryLimit": spec.raw['execution']['retry_limit'],
                                                                     "modelTokens": 0},
                       "auditHooks": ["启动与镜像构建时校验规格与实现逐条对应（不一致拒绝运行）",
                                      "每个检查的开始、结束、重试、失败、降级写入 run.log.jsonl",
                                      "00_MANIFEST.yaml 记录全部输出文件的 SHA-256"]},
        "subAgents": [
            {"name": "doc_claim_extractor", "enabledBy": "--use-llm 且数据集目录中有说明文档",
             "responsibility": "读说明文档，抽取可核对的声明（样本数、类别、时段、尺寸、模态）",
             "inputContract": f"文档正文（最多 {spec.raw['llm']['max_doc_chars']} 字符）+ 规格 llm.prompts.doc_claim_extractor",
             "outputContract": '{"claims": [{"field", "value", "quote"}]}，quote 为文档原文摘录',
             "delegationCondition": "D04 执行且模型服务可用", "checkIds": ["D04"],
             "contextIndependent": True, "producesEvidence": "只产出声明与原文摘录；不通过的结论由 D04 的比对程序产出",
             "arbitration": f"同一文档调用 {spec.raw['llm']['agreement_calls']} 次，只采用完全一致的声明；不一致落待判定；"
                            "摘录在文档中找不到的声明丢弃；与规则结论冲突时以规则为准（模型不出结论）"},
            {"name": "remediation_writer", "enabledBy": "--use-llm",
             "responsibility": "把评分卡主要缺陷的整改建议改写得更具体、可执行",
             "inputContract": "检查名称、缺陷类型、数量、示例说明、规格中的通用整改建议 + llm.prompts.remediation_writer",
             "outputContract": "不超过 80 字的整改步骤正文", "delegationCondition": "全部结论确定之后", "checkIds": [],
             "contextIndependent": True, "producesEvidence": "否：只写入评分卡 remediationDetail 字段",
             "arbitration": "不参与任何判定与计分；调用失败则保留规格中的通用整改建议"},
        ],
        "subAgentsNote": "两个子代理均默认关闭（模型服务消耗 0）；只负责读文档与改写表述，计量、判定、计分、排序全部由确定性程序完成",
    }


FILES = {"manifest.yaml": manifest_yaml, "tools.yaml": tools_yaml, "skills.yaml": skills_yaml,
         "rules.yaml": rules_yaml, "memory.yaml": memory_yaml, "plugins.yaml": plugins_yaml}


def render(spec) -> dict[str, str]:
    return {name: HEADER + yaml.safe_dump(fn(spec), allow_unicode=True, sort_keys=False, width=1000)
            for name, fn in FILES.items()}


def main() -> int:
    spec = load_spec()
    out = render(spec)
    d = ROOT / "agent"
    if "--check" in sys.argv:
        bad = [n for n, t in out.items() if not (d / n).exists() or (d / n).read_text(encoding="utf-8") != t]
        if bad:
            print(f"agent/ 与生成结果不同步：{bad}", file=sys.stderr)
            return 1
        return 0
    d.mkdir(exist_ok=True)
    for n, t in out.items():
        (d / n).write_text(t, encoding="utf-8")
    print(f"已生成 agent/ 下 {len(out)} 个登记文件")
    return 0


if __name__ == "__main__":
    sys.exit(main())
