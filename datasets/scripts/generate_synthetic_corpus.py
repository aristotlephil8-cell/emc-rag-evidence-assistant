"""Build the deterministic public synthetic EMC evaluation corpus.

The fixture intentionally contains no copied standards, customer data, or claims
about a real laboratory.  PDF and DOCX bytes are produced with the Python
standard library so that the committed corpus can be regenerated offline.
"""

from __future__ import annotations

import hashlib
import json
import re
import textwrap
import zipfile
import zlib
from pathlib import Path
from typing import Any, Iterable
from xml.sax.saxutils import escape as xml_escape


CORPUS_VERSION = "verified-synthetic-v2"
FIXED_CREATED_AT = "2026-08-20T00:00:00Z"
NOTICE = "SYNTHETIC TRAINING FIXTURE - NOT A REAL STANDARD OR CERTIFICATION RECORD"


DOCUMENT_SPECS: list[dict[str, str]] = [
    {
        "document_id": "syn-pdf-text-01",
        "filename": "synthetic_conducted_prescan.pdf",
        "format": "pdf",
        "pdf_mode": "text",
        "role": "primary",
        "title": "SYN-EMC-LAB-101 Conducted Pre-scan Teaching Card",
    },
    {
        "document_id": "syn-pdf-text-02",
        "filename": "synthetic_bonding_shielding.pdf",
        "format": "pdf",
        "pdf_mode": "text",
        "role": "primary",
        "title": "SYN-EMC-LAB-202 Bonding and Shielding Teaching Card",
    },
    {
        "document_id": "syn-pdf-scan-01",
        "filename": "synthetic_esd_scan.pdf",
        "format": "pdf",
        "pdf_mode": "scan",
        "role": "primary",
        "title": "SYN-EMC-LAB-303 ESD Observation Sheet",
    },
    {
        "document_id": "syn-pdf-scan-02",
        "filename": "synthetic_cable_scan.pdf",
        "format": "pdf",
        "pdf_mode": "scan",
        "role": "primary",
        "title": "SYN-EMC-LAB-404 Cable Coupling Sheet",
    },
    {
        "document_id": "syn-pdf-table-01",
        "filename": "synthetic_priority_table.pdf",
        "format": "pdf",
        "pdf_mode": "table",
        "role": "primary",
        "title": "SYN-EMC-LAB-505 Pre-scan Priority Table",
    },
    {
        "document_id": "syn-pdf-table-02",
        "filename": "synthetic_immunity_table.pdf",
        "format": "pdf",
        "pdf_mode": "table",
        "role": "primary",
        "title": "SYN-EMC-LAB-606 Immunity Retest Table",
    },
    {
        "document_id": "syn-docx-01",
        "filename": "synthetic_filter_retest.docx",
        "format": "docx",
        "pdf_mode": "not_applicable",
        "role": "primary",
        "title": "SYN-EMC-LAB-707 滤波复测教学记录",
    },
    {
        "document_id": "syn-docx-02",
        "filename": "synthetic_chamber_setup.docx",
        "format": "docx",
        "pdf_mode": "not_applicable",
        "role": "primary",
        "title": "SYN-EMC-LAB-808 场地布置教学记录",
    },
    {
        "document_id": "syn-docx-03",
        "filename": "synthetic_diagnosis_notes.docx",
        "format": "docx",
        "pdf_mode": "not_applicable",
        "role": "primary",
        "title": "SYN-EMC-LAB-909 定位假设教学记录",
    },
    {
        "document_id": "syn-docx-04",
        "filename": "synthetic_current_probe.docx",
        "format": "docx",
        "pdf_mode": "not_applicable",
        "role": "primary",
        "title": "SYN-EMC-LAB-1001 电流路径教学记录",
    },
    {
        "document_id": "syn-docx-05",
        "filename": "synthetic_change_closure.docx",
        "format": "docx",
        "pdf_mode": "not_applicable",
        "role": "primary",
        "title": "SYN-EMC-LAB-1102 整改闭环教学记录",
    },
    {
        "document_id": "syn-txt-primary-01",
        "filename": "synthetic_reporting_boundary.txt",
        "format": "txt",
        "pdf_mode": "not_applicable",
        "role": "primary",
        "title": "SYN-EMC-LAB-1203 报告边界说明",
    },
    {
        "document_id": "syn-txt-distractor-01",
        "filename": "distractor_marketing_glossary.txt",
        "format": "txt",
        "pdf_mode": "not_applicable",
        "role": "distractor",
        "title": "干扰项：市场术语表",
    },
    {
        "document_id": "syn-txt-distractor-02",
        "filename": "distractor_retired_draft.txt",
        "format": "txt",
        "pdf_mode": "not_applicable",
        "role": "distractor",
        "title": "干扰项：已废弃虚构草案",
    },
    {
        "document_id": "syn-txt-distractor-03",
        "filename": "distractor_thermal_fixture.txt",
        "format": "txt",
        "pdf_mode": "not_applicable",
        "role": "distractor",
        "title": "干扰项：热学夹具记录",
    },
    {
        "document_id": "syn-txt-distractor-04",
        "filename": "distractor_maintenance_calendar.txt",
        "format": "txt",
        "pdf_mode": "not_applicable",
        "role": "distractor",
        "title": "干扰项：维护日历",
    },
]


# Facts 1-24 are ASCII on purpose: they are rendered into deterministic PDFs,
# including two image-only PDFs that exercise OCR fallback.
FACT_SPECS: list[dict[str, str]] = [
    {
        "statement": "On synthetic bench C1, wait 60 seconds for LISN stabilization before logging.",
        "answer": "合成台架 C1 在记录前等待 60 秒。",
    },
    {
        "statement": "The teaching sweep band is 150 kHz to 30 MHz.",
        "answer": "教学扫频范围为 150 kHz 至 30 MHz。",
    },
    {
        "statement": "Run a peak-detector scan first, then perform a quasi-peak recheck.",
        "answer": "先做峰值检波扫描，再做准峰值复核。",
    },
    {
        "statement": "Store one cable-layout photograph with every synthetic pre-scan record.",
        "answer": "每条合成预扫记录都要保存一张线缆布置照片。",
    },
    {
        "statement": "The synthetic service-panel seam teaching target is no more than 0.8 mm.",
        "answer": "服务面板接缝教学目标不超过 0.8 mm。",
    },
    {
        "statement": "Use a short flat bonding strap instead of a long round wire in this teaching setup.",
        "answer": "教学布置采用短而扁的跨接带，不采用长圆导线。",
    },
    {
        "statement": "Remove coating at the bonding contact before the torque check.",
        "answer": "扭矩检查前先去除搭接接触处涂层。",
    },
    {
        "statement": "After a shielding change, repeat the same antenna position and cable layout.",
        "answer": "屏蔽改动后使用相同天线位置和线缆布置复测。",
    },
    {
        "statement": "START THE SYNTHETIC ESD SEQUENCE WITH CONTACT DISCHARGE ON CONDUCTIVE POINTS.",
        "answer": "合成 ESD 流程先对导电点做接触放电。",
    },
    {
        "statement": "USE AIR DISCHARGE ONLY FOR INSULATING SURFACES OR INACCESSIBLE CONDUCTIVE POINTS.",
        "answer": "绝缘表面或不可接近导电点才采用空气放电。",
    },
    {
        "statement": "THE DEMO RECOVERY LOG RECORDS THE RESET TYPE AND TIMESTAMP.",
        "answer": "恢复日志记录复位类型与时间戳。",
    },
    {
        "statement": "IF BEHAVIOR CHANGES AFTER DISCHARGE, SAVE THE WAVEFORM AND OPERATOR NOTE BEFORE POWER CYCLING.",
        "answer": "放电后行为改变时，断电重启前保存波形和操作员备注。",
    },
    {
        "statement": "ROUTE THE HIGH DV DT SWITCH NODE AWAY FROM THE SENSOR RIBBON.",
        "answer": "高 dv/dt 开关节点要远离传感器排线。",
    },
    {
        "statement": "CROSS UNAVOIDABLE POWER AND SIGNAL ROUTES AT ABOUT 90 DEGREES.",
        "answer": "无法避开的电源线与信号线应近似 90 度交叉。",
    },
    {
        "statement": "PLACE THE SYNTHETIC CURRENT CLAMP 50 MM FROM CONNECTOR C7.",
        "answer": "合成电流钳放在距连接器 C7 50 mm 处。",
    },
    {
        "statement": "FOR THIS DEMO, THE CURRENT PROBE ARROW POINTS TOWARD THE EQUIPMENT UNDER TEST.",
        "answer": "本演示中电流探头箭头朝向受试设备。",
    },
    {
        "statement": "Assign P1 when the repeatable synthetic margin is below minus 3 dB.",
        "answer": "可重复合成裕量低于 -3 dB 时定为 P1。",
    },
    {
        "statement": "Assign P2 when the repeatable synthetic margin is from minus 3 dB through plus 3 dB.",
        "answer": "可重复合成裕量在 -3 dB 至 +3 dB 时定为 P2。",
    },
    {
        "statement": "Assign P3 when the repeatable synthetic margin is above plus 3 dB.",
        "answer": "可重复合成裕量高于 +3 dB 时定为 P3。",
    },
    {
        "statement": "An owner field is mandatory before a synthetic issue record can be closed.",
        "answer": "合成问题记录关闭前必须填写负责人字段。",
    },
    {
        "statement": "The synthetic burst exercise applies stimulation to port P2.",
        "answer": "合成脉冲群练习对端口 P2 施加激励。",
    },
    {
        "statement": "Use a dwell time of 3 seconds per frequency step in the teaching sweep.",
        "answer": "教学扫频每个频点驻留 3 秒。",
    },
    {
        "statement": "An unintended reset is a failure criterion in this synthetic exercise.",
        "answer": "非预期复位属于该合成练习的失败判据。",
    },
    {
        "statement": "The retest must use the locked fictional firmware build DEMO-7A.",
        "answer": "复测必须使用锁定的虚构固件 DEMO-7A。",
    },
    {
        "statement": "本合成教学记录规定 C14 的参考电容值为 4.7 nF。",
        "answer": "C14 的合成参考值为 4.7 nF。",
    },
    {
        "statement": "虚构磁珠 F2 安装在连接器的线束侧。",
        "answer": "F2 位于连接器线束侧。",
    },
    {
        "statement": "滤波改动前后对比必须使用相同的接收机带宽。",
        "answer": "改动前后保持相同接收机带宽。",
    },
    {
        "statement": "公开合成场地布置中的测试天线距离为 3 m。",
        "answer": "合成布置的天线距离为 3 m。",
    },
    {"statement": "合成转台每次旋转 45 度。", "answer": "转台步进为 45 度。"},
    {
        "statement": "每个最大值都要同时记录极化方向与转台角度。",
        "answer": "每个最大值记录极化方向和转台角度。",
    },
    {
        "statement": "如果移动线缆使峰值移动超过 5 MHz，优先检查线缆耦合假设。",
        "answer": "峰值随线缆移动超过 5 MHz 时优先检查线缆耦合。",
    },
    {
        "statement": "如果峰值不随负载变化但跟随时钟变化，优先检查时钟谐波假设。",
        "answer": "峰值跟随时钟而不随负载时优先检查时钟谐波。",
    },
    {
        "statement": "近场探头在本合成流程中只用于定位，不用于符合性判定。",
        "answer": "近场探头仅用于定位，不用于符合性判定。",
    },
    {
        "statement": "电流探头基线在辅助线缆断开时采集。",
        "answer": "辅助线缆断开时采集电流探头基线。",
    },
    {"statement": "辅助线缆应一次只重新连接一根。", "answer": "辅助线缆逐根重新连接。"},
    {
        "statement": "重新连接后上升超过 6 dB 的路径进入整改复核。",
        "answer": "上升超过 6 dB 的路径进入整改复核。",
    },
    {
        "statement": "整改记录必须写明元件位号和元件值。",
        "answer": "整改记录写明元件位号与元件值。",
    },
    {
        "statement": "关闭问题前必须确认复测沿用与基线相同的布置标识。",
        "answer": "关闭前确认复测与基线使用同一布置标识。",
    },
    {
        "statement": "未采用的整改方案及其拒绝原因也要保留在决策日志中。",
        "answer": "决策日志保留未采用方案及拒绝原因。",
    },
    {
        "statement": "最终合成报告必须标注：仅用于训练，不构成认证证据。",
        "answer": "最终报告标注仅用于训练，不构成认证证据。",
    },
]


CASE_SPECS: list[dict[str, Any]] = [
    {
        "case_id": "syn-q001",
        "split": "dev",
        "scenario_group": "dev-conducted-prescan",
        "question": "[合成题] 台架 C1 开始记录前等待多久，教学扫频范围又是什么？",
        "facts": [1, 2],
    },
    {
        "case_id": "syn-q002",
        "split": "dev",
        "scenario_group": "dev-conducted-prescan",
        "question": "[合成题] 预扫与复核分别采用哪种检波顺序？",
        "facts": [3],
    },
    {
        "case_id": "syn-q003",
        "split": "dev",
        "scenario_group": "dev-conducted-prescan",
        "question": "[合成题] 每条预扫记录还需保存哪一种布置证据？",
        "facts": [4],
    },
    {
        "case_id": "syn-q004",
        "split": "dev",
        "scenario_group": "dev-conducted-prescan",
        "question": "[合成题] SYN-EMC-LAB-101 指定了哪家法定认证机构？",
        "facts": [],
    },
    {
        "case_id": "syn-q005",
        "split": "dev",
        "scenario_group": "dev-bonding-shielding",
        "question": "[合成题] 服务面板接缝目标是多少，跨接带应选择什么形状？",
        "facts": [5, 6],
    },
    {
        "case_id": "syn-q006",
        "split": "dev",
        "scenario_group": "dev-bonding-shielding",
        "question": "[合成题] 搭接点在扭矩检查前要完成什么表面处理？",
        "facts": [7],
    },
    {
        "case_id": "syn-q007",
        "split": "dev",
        "scenario_group": "dev-bonding-shielding",
        "question": "[合成题] 屏蔽改动后的复测要保持哪两项布置不变？",
        "facts": [8],
    },
    {
        "case_id": "syn-q008",
        "split": "dev",
        "scenario_group": "dev-bonding-shielding",
        "question": "[合成题] 教学资料要求购买哪个品牌型号的铜箔？",
        "facts": [],
    },
    {
        "case_id": "syn-q009",
        "split": "dev",
        "scenario_group": "dev-esd-observation",
        "question": "[合成题] 导电点先采用何种放电，空气放电适用于哪些位置？",
        "facts": [9, 10],
    },
    {
        "case_id": "syn-q010",
        "split": "dev",
        "scenario_group": "dev-esd-observation",
        "question": "[合成题] ESD 恢复日志必须记录哪两个字段？",
        "facts": [11],
    },
    {
        "case_id": "syn-q011",
        "split": "dev",
        "scenario_group": "dev-esd-observation",
        "question": "[合成题] 放电后行为改变时，断电重启之前需要保存什么？",
        "facts": [12],
    },
    {
        "case_id": "syn-q012",
        "split": "dev",
        "scenario_group": "dev-esd-observation",
        "question": "[合成题] 该资料规定的真实认证 ESD 电压等级是多少？",
        "facts": [],
    },
    {
        "case_id": "syn-q013",
        "split": "dev",
        "scenario_group": "dev-cable-coupling",
        "question": "[合成题] 开关节点与传感器排线如何隔离，无法避开的电源线和信号线怎样交叉？",
        "facts": [13, 14],
    },
    {
        "case_id": "syn-q014",
        "split": "dev",
        "scenario_group": "dev-cable-coupling",
        "question": "[合成题] 电流钳距连接器 C7 的教学定位距离是多少？",
        "facts": [15],
    },
    {
        "case_id": "syn-q015",
        "split": "dev",
        "scenario_group": "dev-cable-coupling",
        "question": "[合成题] 本演示中电流探头箭头朝向哪里？",
        "facts": [16],
    },
    {
        "case_id": "syn-q016",
        "split": "dev",
        "scenario_group": "dev-cable-coupling",
        "question": "[合成题] 电流探头的真实校准证书编号是什么？",
        "facts": [],
    },
    {
        "case_id": "syn-q017",
        "split": "dev",
        "scenario_group": "dev-priority-triage",
        "question": "[合成题] 可重复裕量低于 -3 dB 时分配哪个优先级？",
        "facts": [17],
    },
    {
        "case_id": "syn-q018",
        "split": "dev",
        "scenario_group": "dev-priority-triage",
        "question": "[合成题] -3 dB 到 +3 dB 的合成裕量对应哪个等级？",
        "facts": [18],
    },
    {
        "case_id": "syn-q019",
        "split": "dev",
        "scenario_group": "dev-priority-triage",
        "question": "[合成题] 合成裕量高于 +3 dB 时如何标记？",
        "facts": [19],
    },
    {
        "case_id": "syn-q020",
        "split": "dev",
        "scenario_group": "dev-priority-triage",
        "question": "[合成题] 优先级表要求每年由哪一机构审计？",
        "facts": [],
    },
    {
        "case_id": "syn-q021",
        "split": "dev",
        "scenario_group": "dev-immunity-workflow",
        "question": "[合成题] 问题记录关闭前哪个责任字段不可缺少？",
        "facts": [20],
    },
    {
        "case_id": "syn-q022",
        "split": "dev",
        "scenario_group": "dev-immunity-workflow",
        "question": "[合成题] 合成脉冲群练习把激励施加到哪个端口？",
        "facts": [21],
    },
    {
        "case_id": "syn-q023",
        "split": "dev",
        "scenario_group": "dev-immunity-workflow",
        "question": "[合成题] 教学扫频每个频点驻留多长时间？",
        "facts": [22],
    },
    {
        "case_id": "syn-q024",
        "split": "dev",
        "scenario_group": "dev-immunity-workflow",
        "question": "[合成题] 哪一种设备行为被列为失败判据？",
        "facts": [23],
    },
    {
        "case_id": "syn-q025",
        "split": "test",
        "scenario_group": "test-filter-retest",
        "question": "[合成题] 滤波复测要锁定哪一固件版本，C14 的参考值是多少？",
        "facts": [24, 25],
    },
    {
        "case_id": "syn-q026",
        "split": "test",
        "scenario_group": "test-filter-retest",
        "question": "[合成题] 虚构磁珠 F2 位于连接器哪一侧？",
        "facts": [26],
    },
    {
        "case_id": "syn-q027",
        "split": "test",
        "scenario_group": "test-filter-retest",
        "question": "[合成题] 比较滤波改动前后时，接收机哪项设置必须一致？",
        "facts": [27],
    },
    {
        "case_id": "syn-q028",
        "split": "test",
        "scenario_group": "test-filter-retest",
        "question": "[合成题] 资料给出的 C14 供应商批次号是什么？",
        "facts": [],
    },
    {
        "case_id": "syn-q029",
        "split": "test",
        "scenario_group": "test-chamber-setup",
        "question": "[合成题] 公开合成场地的天线距离与转台步进分别是多少？",
        "facts": [28, 29],
    },
    {
        "case_id": "syn-q030",
        "split": "test",
        "scenario_group": "test-chamber-setup",
        "question": "[合成题] 捕获每个最大值时需要共同记录哪两项姿态信息？",
        "facts": [30],
    },
    {
        "case_id": "syn-q031",
        "split": "test",
        "scenario_group": "test-chamber-setup",
        "question": "[合成题] 移动线缆导致峰值位移超过 5 MHz 时，先查哪种耦合假设？",
        "facts": [31],
    },
    {
        "case_id": "syn-q032",
        "split": "test",
        "scenario_group": "test-chamber-setup",
        "question": "[合成题] 该合成场地的正式认可证书编号是多少？",
        "facts": [],
    },
    {
        "case_id": "syn-q033",
        "split": "test",
        "scenario_group": "test-diagnosis-path",
        "question": "[合成题] 峰值跟随时钟但不随负载时先查什么，近场探头能否用于符合性判定？",
        "facts": [32, 33],
    },
    {
        "case_id": "syn-q034",
        "split": "test",
        "scenario_group": "test-diagnosis-path",
        "question": "[合成题] 采集电流探头基线时辅助线缆处于什么状态？",
        "facts": [34],
    },
    {
        "case_id": "syn-q035",
        "split": "test",
        "scenario_group": "test-diagnosis-path",
        "question": "[合成题] 重新接入辅助线缆时应采用怎样的顺序？",
        "facts": [35],
    },
    {
        "case_id": "syn-q036",
        "split": "test",
        "scenario_group": "test-diagnosis-path",
        "question": "[合成题] 定位流程指定的近场探头商业型号是什么？",
        "facts": [],
    },
    {
        "case_id": "syn-q037",
        "split": "test",
        "scenario_group": "test-change-closure",
        "question": "[合成题] 路径上升达到什么条件进入整改复核，整改记录需写明哪些元件信息？",
        "facts": [36, 37],
    },
    {
        "case_id": "syn-q038",
        "split": "test",
        "scenario_group": "test-change-closure",
        "question": "[合成题] 关闭问题前，复测布置要与基线核对什么标识？",
        "facts": [38],
    },
    {
        "case_id": "syn-q039",
        "split": "test",
        "scenario_group": "test-change-closure",
        "question": "[合成题] 没有采用的整改方案应如何留痕？",
        "facts": [39],
    },
    {
        "case_id": "syn-q040",
        "split": "test",
        "scenario_group": "test-change-closure",
        "question": "[合成题] 最终报告必须声明哪一项证据边界？",
        "facts": [40],
    },
]


DISTRACTOR_TEXT = {
    "syn-txt-distractor-01": """合成干扰项：市场术语表\n\n本文件只解释“低噪声”“高性能”等宣传词，不给出任何测试步骤、限值、元件值或认证结论。\n它不是 canonical facts 的来源。\n""",
    "syn-txt-distractor-02": """合成干扰项：已废弃虚构草案\n\n状态：RETIRED / NON-CANONICAL。草案中的旧占位符包括 99 秒、9 m 和 DRAFT-X，均不得用于回答冻结题。\n它不是现实标准，也不是本数据集的真相来源。\n""",
    "syn-txt-distractor-03": """合成干扰项：热学夹具记录\n\n该虚构记录讨论散热片清洁与风扇标签，不包含 EMC 检索题所需证据。\n它不是 canonical facts 的来源。\n""",
    "syn-txt-distractor-04": """合成干扰项：维护日历\n\n该虚构日历只列出房间照明和桌椅检查，不规定仪器校准、实验室认可或法定认证信息。\n它不是 canonical facts 的来源。\n""",
}


FONT_5X7 = {
    "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    "B": ("11110", "10001", "10001", "11110", "10001", "10001", "11110"),
    "C": ("01111", "10000", "10000", "10000", "10000", "10000", "01111"),
    "D": ("11110", "10001", "10001", "10001", "10001", "10001", "11110"),
    "E": ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
    "F": ("11111", "10000", "10000", "11110", "10000", "10000", "10000"),
    "G": ("01111", "10000", "10000", "10111", "10001", "10001", "01111"),
    "H": ("10001", "10001", "10001", "11111", "10001", "10001", "10001"),
    "I": ("11111", "00100", "00100", "00100", "00100", "00100", "11111"),
    "J": ("00111", "00010", "00010", "00010", "10010", "10010", "01100"),
    "K": ("10001", "10010", "10100", "11000", "10100", "10010", "10001"),
    "L": ("10000", "10000", "10000", "10000", "10000", "10000", "11111"),
    "M": ("10001", "11011", "10101", "10101", "10001", "10001", "10001"),
    "N": ("10001", "11001", "10101", "10011", "10001", "10001", "10001"),
    "O": ("01110", "10001", "10001", "10001", "10001", "10001", "01110"),
    "P": ("11110", "10001", "10001", "11110", "10000", "10000", "10000"),
    "Q": ("01110", "10001", "10001", "10001", "10101", "10010", "01101"),
    "R": ("11110", "10001", "10001", "11110", "10100", "10010", "10001"),
    "S": ("01111", "10000", "10000", "01110", "00001", "00001", "11110"),
    "T": ("11111", "00100", "00100", "00100", "00100", "00100", "00100"),
    "U": ("10001", "10001", "10001", "10001", "10001", "10001", "01110"),
    "V": ("10001", "10001", "10001", "10001", "10001", "01010", "00100"),
    "W": ("10001", "10001", "10001", "10101", "10101", "11011", "10001"),
    "X": ("10001", "10001", "01010", "00100", "01010", "10001", "10001"),
    "Y": ("10001", "10001", "01010", "00100", "00100", "00100", "00100"),
    "Z": ("11111", "00001", "00010", "00100", "01000", "10000", "11111"),
    "0": ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
    "1": ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    "2": ("01110", "10001", "00001", "00010", "00100", "01000", "11111"),
    "3": ("11110", "00001", "00001", "01110", "00001", "00001", "11110"),
    "4": ("00010", "00110", "01010", "10010", "11111", "00010", "00010"),
    "5": ("11111", "10000", "10000", "11110", "00001", "00001", "11110"),
    "6": ("01110", "10000", "10000", "11110", "10001", "10001", "01110"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
    "9": ("01110", "10001", "10001", "01111", "00001", "00001", "01110"),
    "-": ("00000", "00000", "00000", "11111", "00000", "00000", "00000"),
    ".": ("00000", "00000", "00000", "00000", "00000", "00110", "00110"),
    ",": ("00000", "00000", "00000", "00000", "00110", "00100", "01000"),
    "/": ("00001", "00010", "00010", "00100", "01000", "01000", "10000"),
    ":": ("00000", "00110", "00110", "00000", "00110", "00110", "00000"),
    "?": ("01110", "10001", "00001", "00010", "00100", "00000", "00100"),
    " ": ("00000",) * 7,
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows
    )
    path.write_text(content, encoding="utf-8", newline="\n")


def _pdf_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _assemble_pdf(objects: list[bytes]) -> bytes:
    output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for number, body in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{number} 0 obj\n".encode("ascii"))
        output.extend(body)
        output.extend(b"\nendobj\n")
    xref_offset = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode(
            "ascii"
        )
    )
    return bytes(output)


def _stream_object(stream: bytes, extra: str = "") -> bytes:
    return (
        f"<< /Length {len(stream)} {extra}>>\nstream\n".encode("ascii")
        + stream
        + b"\nendstream"
    )


def _text_pdf(title: str, sections: list[dict[str, Any]], table: bool) -> bytes:
    commands: list[str] = []
    if table:
        table_top = 700
        row_height = 70
        table_bottom = table_top - len(sections) * row_height
        commands.append("0.8 w")
        for index in range(len(sections)):
            y = table_top - (index + 1) * row_height
            commands.append(f"45 {y} 522 {row_height} re S")
        commands.append(f"145 {table_bottom} m 145 {table_top} l S")
        commands.extend(
            [
                "BT",
                "/F1 10 Tf",
                "50 765 Td",
                f"({_pdf_escape(NOTICE)}) Tj",
                "0 -27 Td",
                f"({_pdf_escape(title)}) Tj",
                "ET",
            ]
        )
        for index, section in enumerate(sections):
            y = 672 - index * row_height
            statement_lines = textwrap.wrap(
                section["statement"], width=69, break_long_words=False
            )
            commands.extend(
                [
                    "BT",
                    "/F1 8 Tf",
                    f"50 {y} Td",
                    f"({_pdf_escape(section['section_id'])}) Tj",
                    "ET",
                ]
            )
            for line_index, line in enumerate(statement_lines[:3]):
                commands.extend(
                    [
                        "BT",
                        "/F1 8 Tf",
                        f"150 {y - line_index * 12} Td",
                        f"({_pdf_escape(line)}) Tj",
                        "ET",
                    ]
                )
    else:
        lines = [NOTICE, title, ""]
        for section in sections:
            lines.append(f"[{section['section_id']}] {section['statement']}")
            lines.append("")
        commands.extend(["BT", "/F1 10 Tf", "50 750 Td"])
        for line in lines:
            for wrapped in textwrap.wrap(line, width=88, break_long_words=False) or [
                ""
            ]:
                commands.append(f"({_pdf_escape(wrapped)}) Tj")
                commands.append("0 -17 Td")
        commands.append("ET")
    content = "\n".join(commands).encode("ascii")
    return _assemble_pdf(
        [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
            _stream_object(content),
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        ]
    )


def _draw_bitmap_line(
    canvas: bytearray, width: int, x: int, y: int, value: str, scale: int = 5
) -> None:
    for char_index, char in enumerate(value.upper()):
        glyph = FONT_5X7.get(char, FONT_5X7["?"])
        origin_x = x + char_index * 6 * scale
        for row, bits in enumerate(glyph):
            for column, bit in enumerate(bits):
                if bit != "1":
                    continue
                for dy in range(scale):
                    start = (y + row * scale + dy) * width + origin_x + column * scale
                    canvas[start : start + scale] = b"\x00" * scale


def _scan_pdf(title: str, sections: list[dict[str, Any]]) -> bytes:
    width, height = 1224, 1584
    canvas = bytearray(b"\xff" * (width * height))
    y = 70
    lines: list[str] = []
    lines.extend(textwrap.wrap(NOTICE, width=36, break_long_words=False))
    lines.extend(textwrap.wrap(title, width=36, break_long_words=False))
    lines.append("")
    for section in sections:
        lines.append(section["section_id"])
        lines.extend(
            textwrap.wrap(section["statement"], width=36, break_long_words=False)
        )
        lines.append("")
    for line in lines:
        _draw_bitmap_line(
            canvas, width, 70, y, re.sub(r"[^A-Za-z0-9 .,/:?-]", " ", line), scale=5
        )
        y += 52
    compressed = zlib.compress(bytes(canvas), level=9)
    content = b"q\n612 0 0 792 0 0 cm\n/Im0 Do\nQ"
    image_extra = (
        f"/Type /XObject /Subtype /Image /Width {width} /Height {height} "
        "/ColorSpace /DeviceGray /BitsPerComponent 8 /Filter /FlateDecode "
    )
    return _assemble_pdf(
        [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /XObject << /Im0 5 0 R >> >> /Contents 4 0 R >>",
            _stream_object(content),
            _stream_object(compressed, image_extra),
        ]
    )


def _docx_paragraph(text: str, style: str | None = None) -> str:
    style_xml = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    return f'<w:p>{style_xml}<w:r><w:t xml:space="preserve">{xml_escape(text)}</w:t></w:r></w:p>'


def _docx_bytes(title: str, sections: list[dict[str, Any]]) -> bytes:
    from io import BytesIO

    paragraphs = [_docx_paragraph(NOTICE), _docx_paragraph(title, "Title")]
    for section in sections:
        paragraphs.append(_docx_paragraph(section["section_id"], "Heading1"))
        paragraphs.append(_docx_paragraph(section["statement"]))
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f'<w:body>{"".join(paragraphs)}<w:sectPr><w:pgSz w:w="11906" w:h="16838"/></w:sectPr></w:body></w:document>'
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    relationships = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        "</Relationships>"
    )
    buffer = BytesIO()
    with zipfile.ZipFile(
        buffer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for name, content in (
            ("[Content_Types].xml", content_types),
            ("_rels/.rels", relationships),
            ("word/document.xml", document_xml),
        ):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 0
            archive.writestr(
                info,
                content.encode("utf-8"),
                compress_type=zipfile.ZIP_DEFLATED,
                compresslevel=9,
            )
    return buffer.getvalue()


def _region(
    document: dict[str, Any],
    section_index: int,
    section_id: str,
    statement: str,
) -> dict[str, Any]:
    if document["format"] == "pdf":
        if document["pdf_mode"] == "table":
            top = 0.10 + section_index * 0.088
            return {
                "kind": "table_row",
                "page_bbox": [0.07, round(top, 3), 0.93, round(top + 0.082, 3)],
                "table_row": section_index + 1,
            }
        if document["pdf_mode"] == "scan":
            line_index = (
                len(textwrap.wrap(NOTICE, width=36, break_long_words=False))
                + len(
                    textwrap.wrap(document["title"], width=36, break_long_words=False)
                )
                + 1
            )
            for previous in document["sections"]:
                line_index += 2 + len(
                    textwrap.wrap(
                        previous["statement"],
                        width=36,
                        break_long_words=False,
                    )
                )
            statement_lines = textwrap.wrap(
                statement,
                width=36,
                break_long_words=False,
            )
            region_line_count = 1 + len(statement_lines)
            top = (70 + line_index * 52 - 5) / 1584
            bottom = (70 + (line_index + region_line_count - 1) * 52 + 40) / 1584
            return {
                "kind": "page_bbox",
                "page_bbox": [0.05, round(top, 4), 0.95, round(bottom, 4)],
            }

        line_index = 3
        for previous in document["sections"]:
            previous_text = f"[{previous['section_id']}] {previous['statement']}"
            line_index += (
                len(textwrap.wrap(previous_text, width=88, break_long_words=False)) + 1
            )
        current_text = f"[{section_id}] {statement}"
        line_count = len(textwrap.wrap(current_text, width=88, break_long_words=False))
        top = (34.0 + line_index * 17 - 2.0) / 792
        bottom = (44.5 + (line_index + line_count - 1) * 17 + 2.0) / 792
        return {
            "kind": "page_bbox",
            "page_bbox": [0.07, round(top, 4), 0.93, round(bottom, 4)],
        }
    if document["format"] == "docx":
        top = 0.14 + section_index * 0.18
        return {
            "kind": "paragraph",
            "page_bbox": [0.08, round(top, 3), 0.92, round(top + 0.12, 3)],
            "paragraph_index": 4 + section_index * 2,
        }
    return {
        "kind": "line_span",
        "line_start": 4 + section_index * 3,
        "line_end": 5 + section_index * 3,
    }


def _materialize_specs() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    documents = [dict(spec, sections=[]) for spec in DOCUMENT_SPECS]
    by_id = {document["document_id"]: document for document in documents}
    primary = [document for document in documents if document["role"] == "primary"]
    facts: list[dict[str, Any]] = []
    fact_cursor = 0
    capacities = [4, 4, 4, 4, 4, 4, 3, 3, 3, 3, 3, 1]
    for document, capacity in zip(primary, capacities, strict=True):
        for section_index in range(capacity):
            spec = FACT_SPECS[fact_cursor]
            number = fact_cursor + 1
            fact_id = f"syn-fact-{number:04d}"
            evidence_id = f"syn-ev-{number:04d}"
            section_id = f"{document['document_id']}-sec-{section_index + 1:02d}"
            region = _region(
                document,
                section_index,
                section_id,
                spec["statement"],
            )
            fact = {
                "fact_id": fact_id,
                "evidence_id": evidence_id,
                "truth_status": "canonical_synthetic",
                "statement": spec["statement"],
                "canonical_answer": spec["answer"],
                "locator": {
                    "document_id": document["document_id"],
                    "section_id": section_id,
                    "page_number": 1,
                    "region": region,
                },
            }
            facts.append(fact)
            document["sections"].append(
                {
                    "section_id": section_id,
                    "page_number": 1,
                    "region": region,
                    "statement": spec["statement"],
                    "fact_ids": [fact_id],
                    "evidence_ids": [evidence_id],
                }
            )
            fact_cursor += 1
    if fact_cursor != len(FACT_SPECS):
        raise AssertionError("fact capacities do not consume all canonical facts")
    for document_id in DISTRACTOR_TEXT:
        by_id[document_id]["sections"] = [
            {
                "section_id": f"{document_id}-sec-01",
                "page_number": 1,
                "region": {"kind": "line_span", "line_start": 1, "line_end": 4},
                "statement": "NON-CANONICAL SYNTHETIC DISTRACTOR",
                "fact_ids": [],
                "evidence_ids": [],
            }
        ]
    return documents, facts


def _build_cases(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_number = {index + 1: fact for index, fact in enumerate(facts)}
    cases: list[dict[str, Any]] = []
    for spec in CASE_SPECS:
        evidence = []
        answers = []
        for fact_number in spec["facts"]:
            fact = by_number[fact_number]
            evidence.append(
                {
                    "evidence_id": fact["evidence_id"],
                    "fact_id": fact["fact_id"],
                    **fact["locator"],
                }
            )
            answers.append(fact["canonical_answer"])
        answerable = bool(evidence)
        cases.append(
            {
                "case_id": spec["case_id"],
                "dataset_label": "VERIFIED_SYNTHETIC",
                "split": spec["split"],
                "scenario_group": spec["scenario_group"],
                "question": spec["question"],
                "answerable": answerable,
                "gold": {
                    "expected_answer": "；".join(answers) if answerable else None,
                    "expected_disposition": "answer"
                    if answerable
                    else "insufficient_evidence",
                    "required_evidence": evidence,
                },
            }
        )
    return cases


def generate_all(repo_root: Path) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    documents_dir = repo_root / "datasets" / "generated" / "documents"
    documents_dir.mkdir(parents=True, exist_ok=True)
    evaluation_dir = repo_root / "evaluation"
    evaluation_dir.mkdir(parents=True, exist_ok=True)
    documents, facts = _materialize_specs()

    for document in documents:
        destination = documents_dir / document["filename"]
        sections = document["sections"]
        if document["format"] == "pdf":
            if document["pdf_mode"] == "scan":
                payload = _scan_pdf(document["title"], sections)
            else:
                payload = _text_pdf(
                    document["title"], sections, table=document["pdf_mode"] == "table"
                )
            destination.write_bytes(payload)
        elif document["format"] == "docx":
            destination.write_bytes(_docx_bytes(document["title"], sections))
        else:
            if document["role"] == "distractor":
                content = DISTRACTOR_TEXT[document["document_id"]]
            else:
                lines = [NOTICE, document["title"], ""]
                for section in sections:
                    lines.extend(
                        [f"[{section['section_id']}]", section["statement"], ""]
                    )
                content = "\n".join(lines)
            destination.write_text(content, encoding="utf-8", newline="\n")

    facts_path = repo_root / "datasets" / "generated" / "canonical_facts.jsonl"
    _write_jsonl(facts_path, facts)
    for document in documents:
        document_path = documents_dir / document["filename"]
        document["artifact"] = {
            "path": document_path.relative_to(repo_root).as_posix(),
            "sha256": _sha256(document_path),
            "size_bytes": document_path.stat().st_size,
        }
    corpus_manifest = {
        "schema_version": 1,
        "corpus_version": CORPUS_VERSION,
        "dataset_label": "VERIFIED_SYNTHETIC",
        "created_at": FIXED_CREATED_AT,
        "notice": NOTICE,
        "counts": {
            "logical_documents": 16,
            "primary_documents": 12,
            "distractor_documents": 4,
            "pdf_documents": 6,
            "pdf_scan": 2,
            "pdf_table": 2,
            "pdf_text": 2,
            "docx_documents": 5,
            "txt_documents": 5,
            "canonical_facts": 40,
        },
        "canonical_facts": {
            "path": facts_path.relative_to(repo_root).as_posix(),
            "sha256": _sha256(facts_path),
            "size_bytes": facts_path.stat().st_size,
        },
        "documents": documents,
    }
    manifest_path = repo_root / "datasets" / "generated" / "corpus_manifest.json"
    _write_json(manifest_path, corpus_manifest)

    cases = _build_cases(facts)
    cases_path = evaluation_dir / "frozen_cases.jsonl"
    _write_jsonl(cases_path, cases)
    frozen_paths = [
        *(documents_dir / document["filename"] for document in documents),
        facts_path,
        manifest_path,
        cases_path,
    ]
    freeze_manifest = {
        "schema_version": 1,
        "corpus_version": CORPUS_VERSION,
        "dataset_label": "VERIFIED_SYNTHETIC",
        "created_at": FIXED_CREATED_AT,
        "hash_algorithm": "sha256",
        "generator": {
            "path": Path(__file__).resolve().relative_to(repo_root).as_posix()
            if Path(__file__).resolve().is_relative_to(repo_root)
            else "datasets/scripts/generate_synthetic_corpus.py",
            "sha256": _sha256(Path(__file__).resolve()),
        },
        "counts": {
            "logical_documents": 16,
            "cases": 40,
            "answerable": 32,
            "unanswerable": 8,
            "dev": 24,
            "test": 16,
            "required_evidence": 40,
        },
        "files": [
            {
                "path": path.relative_to(repo_root).as_posix(),
                "sha256": _sha256(path),
                "size_bytes": path.stat().st_size,
            }
            for path in sorted(
                frozen_paths, key=lambda item: item.relative_to(repo_root).as_posix()
            )
        ],
    }
    freeze_path = evaluation_dir / "freeze_manifest.json"
    _write_json(freeze_path, freeze_manifest)
    return freeze_manifest


def main() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    result = generate_all(repo_root)
    print(
        json.dumps(
            {"status": "generated", "counts": result["counts"]}, ensure_ascii=False
        )
    )


if __name__ == "__main__":
    main()
