# JSON 配置参考 · ESS WaveTest 1.3.0

全部时间值单位为 **秒**，通道 `skew_us` 来源于 CFG，单位为微秒。数值必须是有限 JSON 数字，不接受数字字符串、NaN、Infinity 或用布尔值冒充数字。
未知字段会报错，避免拼写错误被静默忽略。JSON 不支持注释。标识符区分大小写。

## 顶层

`schema_version` 必须是整数 `1`；`name` 是非空套件名称；`channels` 是非空业务别名映射；`tests` 包含 1..1000 个断言。
可选 `description`；可选 `standard`。

`standard` 可放在套件层或单项断言层，字段为：`reference`（标准编号/版次或项目规约）、`clause`（具体条款）、
`reviewed`（布尔，默认未审核）、`reviewer`（审核人）、`note`（适用对象/条件/解释）。
`reviewed=true` 时必须同时提供非空 reference、clause、reviewer。这个字段是用户声明，不是软件认证或电子签名。

## 通道别名

每项包括 `kind: "analog" | "digital"`，且仅使用一种选择器：

| 选择器 | 示例 | 行为 |
|---|---|---|
| `name` | `"有功功率"` | 精确匹配 |
| `names` | `["P反馈", "有功功率"]` | 备选名中仍必须只匹配一个通道 |
| `index` | `3` | 本类型内从 1 开始的序号，不是总通道位置 |
| `pattern` | `"PCS1_*P*"` | fnmatch 风格的 `*`、`?`、字符集合；不是正则表达式 |

零匹配、多匹配、类型不匹配均为 ERROR，不会默认选第一条。

模拟量可选字段：`unit` 检查 CFG 原始单位；`scale` 默认 1；`offset` 默认 0；
`side` 为 `native`（默认）、`primary`、`secondary`；`output_unit` 为显式换算后的显示/比较单位。
当 output_unit 与 CFG 单位不同，必须显式提供 scale；软件不猜测单位换算或量纲是否合理。
例如原始 W 转换为 kW：`{"kind":"analog","name":"P","unit":"W","scale":0.001,"output_unit":"kW"}`。
scale/offset 也可用于明确的极性和校准修正。工程值处理顺序：

```
CFG 工程值 = a * 原始采样值 + b
目标侧工程值 = CFG 工程值 * 显式一次/二次变比
最终值 = 目标侧工程值 * scale + offset
```

若数据记录在 S 侧且要求 primary，乘 primary/secondary；反向则乘 secondary/primary。
1991 CFG 缺少该侧别/变比元数据，因此只允许 native。模拟量 CFG 单位不能为空。
数字量禁止 unit、output_unit、scale、offset、side；有效电平应在数字断言中声明。

## 公共断言字段

必须有唯一非空 `id`、`type`、`channel`（业务别名）、`window: [开始,结束]`；窗口结束必须大于开始。
可选 `name`、`description`、`trigger`、`standard`、`max_gap_s`。

`window` 相对于该断言的触发时刻，而不是相对于文件 CFG 的 trigger 字段自动计算。
未声明 trigger 时，使用 `{"type":"time","at":0}`。本版不自动把 CFG 触发时刻当作每项试验起点。
如需使用 CFG 的触发时刻，将 inspect 中的 `trigger_s` 明确填入 time.at。

`max_gap_s` 是额外的最大允许采样间隔，超限为 INCONCLUSIVE；解析器已发现的疑似间隙即使不设置此项也会阻止通过。
窗口不会自动裁剪到录波范围，不会补点后假装记录完整。

## 触发器

固定时间：`{"type":"time","at":0.2}`。

数字变位：`{"type":"digital_edge","channel":"cmd","edge":"rising","occurrence":1,"debounce_s":0.002,"search":[0,1]}`。

模拟阈值：`{"type":"analog_crossing","channel":"voltage","threshold":180,"edge":"falling","occurrence":1,"search":[0,2]}`。

事件触发 edge 为 rising/falling，默认 rising；occurrence 默认 1，从 1 开始。
search 是相对录波起点的绝对秒窗口，默认该触发通道的全部有效时间范围。模拟阈值使用线性插值。
没有找到触发事件时为 INCONCLUSIVE：无法确认试验激励执行，不能据此直接宣布设备失败。
搜索窗口中发现缺失值/采样间隙也会阻止可靠触发定位。多次动作应分成多项断言并设置 occurrence/search。

## 数字量

### digital_edge

`edge`: rising/falling/any，默认 rising；`count` 预期精确次数，默认 1，可为 0；
`debounce_s` 默认 0；可选 `latency_min_s`、`latency_max_s`；可选 `guard_band_s` 默认 0。
延时等于该变位原始前沿采样时间减触发时间，多次匹配时每次分别判断。
要求消抖时，原始前沿需要后续稳定保持才能确认；确认后报告的仍是原始前沿时间，不把去抖等待时间混入反馈时延。

### digital_pulse

`active` 为 0/1，默认 1；`count` 默认 1，可为 0；至少提供 `min_width_s`、`max_width_s` 之一；
可选 `debounce_s`、`guard_band_s`。
每个脉冲必须同时观察到有效前沿和后沿，宽度为两者采样时间差。
窗口开始时已处于有效电平但没有可用前沿，或窗口末端尚未复位，均为 INCONCLUSIVE，而不是静默丢弃。

### digital_hold

`value` 必须是 0 或 1；可选 `debounce_s`，默认 0。
默认对原始数字量逐点检查，即使只有一个采样点的错误状态也会失败。
显式设置去抖后，短于去抖时间的脉冲可被忽略；这会改变“保持不变”的含义，需由用例负责人确认。
末端未获得足够观察时长的状态变化不会判为通过。

数字量只测得采样时间上的边沿；边沿真实发生时刻一般介于相邻样本之间，不能声称无限时间精度。
guard_band_s > 0 时，在限值两侧该保护带内返回 INCONCLUSIVE，供人工复核；该值不是自动计算的测量系统不确定度。

## 模拟量范围与 RMS

`analog_range`：至少提供 `min`、`max` 之一，检查窗口内分段线性重构的全部端点/采样极值；输出 min、max、时间加权 mean。

`analog_rms`：至少提供 `min`、`max` 之一；输出整个指定窗口的有效值。
采用 **原信号分段线性重构后，对其平方精确积分** 的约定：

```
RMS = sqrt( sum_i [dt_i * (y_i^2 + y_i*y_(i+1) + y_(i+1)^2) / 3] / T )
```

不是滑动 RMS、PLL 同步窗或自动电能质量标准测量算法。稀疏采样的正弦，分段线性重构的有效值会略低于理想连续正弦；
如项目标准指定另一种计算方法，需先对齐实现与样本。输入已经是设备导出的 RMS 通道时，应使用 analog_range/analog_step，不再对其做一次 RMS。

## analog_step

分析对象应是功率、直流量或已计算好的 RMS/幅值反馈等有明确工程含义的标量，而不是直接套用瞬时交流正弦。

必填：`target` 或 `target_channel` 二选一；`baseline_window`；`limits`；window 的开始必须为 0。

使用 target_channel 时另需 `target_window`，相对触发时刻；目标值等于该窗口的时间加权平均；
目标窗口峰峰值超过 `target_tolerance`（默认 0）时返回 INCONCLUSIVE，避免把多阶跃目标当作单阶跃。
目标通道与反馈通道的最终单位必须相同。目标窗口和基线窗口均须在录波中完整覆盖。

| 字段 | 默认 | 含义 |
|---|---:|---|
| `normalization` | `step` | `step`=阶跃幅值绝对值；`rated`=额定值；`target`=目标绝对值 |
| `rated` | 无 | normalization=rated 时必须大于 0 |
| `steady_last_s` | 0.2 | 分析窗口末端用于计算稳态统计的长度 |
| `settle_band_pct` | 2 | 以归一化分母为基准的调节误差带百分比 |
| `settle_hold_s` | 0.1 | 调节后至少观察到的持续带内时长 |
| `low_fraction` | 0.1 | 响应幅值低阈值 |
| `high_fraction` | 0.9 | 响应幅值高阈值 |
| `guard_band_s` | 0 | 时间指标限值保护带 |

B=基线时间加权均值，T=目标，A=T-B，D=所选归一化分母。A≈0 时阶跃未定义，返回 INCONCLUSIVE。
T=0 且 normalization=target 时为 ERROR；请用 step 或 rated。

- `overshoot_pct` = `100 * max(0, max(sign(A)*(y-T))) / D`，正负阶跃统一处理。
- `latency_s` = 触发至首次低阈值穿越时间；不是检测第一个任意噪声点的时间。
- `response_s` = 触发至首次高阈值穿越时间；默认高阈值 90%。
- `rise_s` = 高阈值穿越时间减低阈值穿越时间，下降阶跃同样按方向归一化。
- `settling_s` = 最后一个带外采样点之后的首个带内采样时间减触发时间，随后须一直处于带内直到分析窗口末端，且满足 settle_hold_s；不把第一次进带当作调节完成。
- `steady_error_pct` = `100 * abs(末端稳态时间加权均值 - T) / D`。
- `steady_ripple_pp` = 末端稳态窗口内最大值减最小值，使用最终工程单位。

limits 支持以上七项各自加 `_max`，例如 `response_s_max`。至少填写一个；没有填写的指标只展示不判限。
未达到时间阈值且观察窗口足够长时相应指标 FAIL；观察不足、或触发时已经响应而无法测得阈值穿越时 INCONCLUSIVE。
任何一个可靠的 FAIL 会使该断言 FAIL；ERROR 优先级高于 FAIL，FAIL 高于 INCONCLUSIVE，最后才是 PASS。

## 解析与限制

DAT 时间戳 × TIMEMULT × 1e-6 得到秒；模拟通道再加 CFG skew_us × 1e-6。
时间戳全部为零或全为缺失标记时，可按 CFG 的分段采样率重建；部分缺失、时间倒退、重复时间戳、样本序号不连续拒绝解析。
以声明采样率为基准识别疑似间隙；无采样率时以时间差中位数估计，并给出警告。
该启发式不是硬件丢点检测器，尤其是不规则事件采样，正式项目应设置 max_gap_s 并审核时间基准。

缺失模拟量按各格式的受支持缺失标记识别为 NaN；包含该值的选中分析窗口返回 INCONCLUSIVE。
没有使用的坏通道不会直接污染其他断言。单个文件故障不阻断批处理中的其他文件。
模拟 ADC 饱和、CT/PT 接线错误、厂家元数据谎报、采样前滤波延迟、绝对时钟精度并未自动校验。

## 输出与代码接口

```python
from esswave.comtrade import load
from esswave.engine import run
from esswave.report import export
import json

record = load("sample.cfg", "sample.dat")
with open("case.json", encoding="utf-8-sig") as f:
    configuration = json.load(f)
result = run(record, configuration)
export(result, "results")
```

HTML 用于人读与打印；JSON 保存完整配置和证据；CSV 汇总指标；JUnit 将 INCONCLUSIVE 输出为 error 而不是 skipped，避免流水线错误放行。
报告与 CSV 会转义不可信输入，CSV 对潜在公式文本做防护。报告 hash 用于复现比对，不是防篡改签章。

## v1.1 边界语义与可视化补充

本节优先于旧版文档中的笼统描述，JSON字段结构未改变。

**上升/下降时间：** `rise_s`的观察时长从低阈值穿越时刻开始，而不是从触发开始。尚未达到高阈值，且自低阈值以来的有效观察长度不足以证明超限时，返回INCONCLUSIVE。没有低阈值穿越则该指标未定义；配置了`response_s_max`等独立指标时，仍可由充分的独立证据判FAIL。

**去抖：** 窗口内数字断言不会使用窗口结束之后的样本完成去抖确认。后续数据追加不能改变同一封闭窗口的既有可观测证据。去抖延迟与动作时延的时间戳口径保持原有约定，未确认边沿/脉冲不得误认为完整可信事件。

**触发边界：** 搜索边界按包含处理。模拟穿越保留左边界前一个采样点作为穿越证据，但不会把已停留在阈值平台上的样本当作新边沿。数字触发检查前置点和确认期连续性；前置证据跨采样间隙会阻止可靠触发。

**有限值：** DAT原始缺失标记与计算溢出不同。已选通道的真实缺失值可导致INCONCLUSIVE；有限值经时间倍率、工程系数、映射比例或计算累计产生非有限值，不得静默转成缺失或PASS。解析阶段溢出拒绝录波，配置换算和执行阶段错误为ERROR。

**缩放：** GUI时间轴以录波起点相对秒显示；预览在完整记录内，结果图在对应断言证据窗口内。返回的窗口可能包括左右各一个边界邻点用于连续曲线，显示被裁剪在输入区间。邻点不代表分析范围扩大。

模拟量宽窗使用保极值抽稀，数字密集变位使用显式包络，窄窗恢复原始点。缺失区间断线，采样间隙显示灰色带。界面缩放不会改用例JSON、计算数据或验收结论。要改变分析窗口，必须修改`window`并重新运行。

复杂用例生成命令：`python ESS_WaveTest.pyz generate-complex --out regenerated_complex`。
清单批回归验证总体状态；`python tools/verify_complex.py`额外验证本包12组复合案例的逐断言状态及独立指标区间。


## v1.2 抗噪算法：显式开启、原始与处理双分支

本节优先于旧版关于噪声/削顶“未检测”的笼统描述。削顶仅在显式给出量程上下限时检查占用比例，不自动识别传感器真量程。所有数值均为用户提供的工程参数，程序不会读取国标原文或自动优化成PASS。

### 1. 适用对象和准入

`robust`只允许放在`analog_step`断言；主要用于功率、直流量或已有物理意义的控制响应量。不要直接用阶跃抗噪模板替代交流瞬时量的周波RMS、谐波或暂态保护分析。`noise`用于digital_edge/digital_pulse/digital_hold及digital_edge触发。旧配置不启用新预处理，原有结果兼容。

缺少有效样本、时间不连续、工程量单位不匹配等先执行原有拒绝/不足规则。抗噪分支不会填补缺失或跨间隙合成数据。质量异常是“不能信赖性能结论”，不是将故障自动当作噪声，更不是证明设备健康。

### 2. 模拟量robust完整字段

| 字段 | 默认/必填 | 单位及语义 |
|---|---|---|
| raw_min / raw_max | 两者必填 | 工程单位；有效分析段和基线段原始硬下/上限，min<max，不滤波 |
| max_noise_sigma | 必填 | 工程单位；原始基线和原始稳态的时间加权1.4826×MAD尺度上限 |
| max_baseline_drift / max_steady_drift | 必填 | 工程单位；各窗口首尾四分之一时间区间中位数之差绝对值上限 |
| max_replaced_fraction | 必填 | 0..0.1；Hampel替换点的时间权重比例上限，基线与分析取较大者；不是平滑修改比例 |
| hampel_window_s | 0=关闭 | 秒，全宽。仅完整落在分析/基线段内的窗口参加候选检测，左右各至少2个点 |
| hampel_sigma | 4.5 | 大于0的候选阈值系数；不是统计置信度 |
| hampel_floor | 开启Hampel必填正数 | 工程单位；候选检测幅值下限，避免MAD为零时过度替换 |
| max_spike_s | 开启Hampel必填正数 | 秒；候选岛两侧未标记邻点之间的时间跨度上限，必须小于Hampel全宽的一半 |
| smooth_window_s | 0=关闭 | 秒，全宽；候选修复后可选居中中值平滑，单独审计改变点数及最大变化 |
| crossing_hold_s | 0 | 秒；低/高阈值穿越后必须持续观察足够时长才确认 |
| crossing_hysteresis_fraction | 0 | 阶跃幅值的比例，0≤值<0.1；确认中回落低于“阈值-滞回”取消候选 |
| settle_occupancy | 1 | 0<值≤1；候选调节时刻到分析结束之间固定带内时间占比 |
| max_excursion_s | 0 | 秒；该尾段最长连续出带时间。与占用率同时约束，不扩大误差带 |
| clip_min / clip_max / max_clip_fraction | 三者一起配置才启用 | 工程量程下/上限；在任一量程边界或之外的时间权重占比上限0..1 |
| metric_guard_pct | 0 | 百分点；超调、稳态误差限值附近进入复核的半宽 |
| ripple_guard | 0 | 工程单位；稳态峰峰值限值附近的复核半宽 |

参数必须有限，除明确允许负数的工程上下限外不得负值。没有未知参数忽略机制，写错字段即ERROR。

候选公式为 `m_i=median(local_values)`，`s_i=1.4826*median(abs(x-m_i))`，当 `abs(x_i-m_i)>max(hampel_floor,hampel_sigma*s_i)` 时标记。局部集合按实际秒选择，不是固定点数；局部median是样本中位数。**本实现只对短、有双侧支撑、两侧水平相近的候选岛做替换**，长扰动和边界扰动不以Hampel自动消除。后续中值平滑是另一项操作，不应误读成只有候选点被改动。

稳态和基线的加权中位数/MAD使用Voronoi时间权重：内部点权重为相邻两个点时间差的一半，首尾各半个相邻间隔。它是对不等间隔样本的明确定义，不是连续过程的精确概率分布。`steady_mean`仍是处理曲线的分段线性时间均值；`steady_median`是稳健估计，`steady_error_pct`按后者计算。查看报告时不要把两者当同一定义。

### 3. 响应与调节时间

对正负阶跃统一转为进度 `q=(x-baseline)/(target-baseline)`。低/高阈值穿越通过邻点线性插值定位，然后持续确认；保存候选起点、确认时刻、实际确认样本和支撑样本。指标时间取穿越起点，而不是把确认时间直接加到响应时间。

时间复核半宽为 `max(guard_band_s,最大采样间隔+Hampel半窗+平滑半窗+触发支撑不确定度)`；上升时间用两倍。它是保守工程复核尺度，**不是经计量溯源的误差界或统计置信区间**。临界值附近不得凭滤波小数位精度强行PASS。

调节时间在分段线性重建上计算固定误差带内外的时长，选择满足尾段保持长度、带内占用率及最长连续出带约束的最早带内区间起点。使用`settle_occupancy<1`本身改变了“持续不出带”的测量定义，必须经项目审核；不是所有标准都允许这一口径。程序不会根据实际噪声加大`settle_band_pct`。

这是离线居中算法，非因果在线滤波。原有`segment`可用窗口端点两侧的原始邻点做线性插值，端点邻点可能位于请求窗口外一个采样；抗噪局部邻域只在分段后的封闭数据中选点，不跨缺失/间隙。数字确认及模拟触发的确认则严格截至搜索/断言终点已有的原生采样。

### 4. 双分支判定与质量检查

原始`raw_metrics`、`raw_checks`不被覆盖。处理分支只用于显式选定的性能测量。原始超调或峰峰值FAIL、处理后相同检查PASS时，添加`raw_peak_review=INCONCLUSIVE`，没有“自动相信噪声”开关。原始硬限值FAIL仍具有FAIL优先级。原始工程值不修改，原始CFG/DAT不重写。

噪声尺度、漂移、削顶占比、Hampel替换比例超预算，或Hampel有效内窗采样不足/审计记录被截断，处理性能检查转INCONCLUSIVE；原始硬限值仍单列。解析/缺失已先阻断分析时，不伪造未知原始区间的硬限值结论。纯数据异常与可靠的独立硬越限要分别处理。

最大局部窗口2001点；Hampel邻域访问2000万次；候选修复和岛审计各最多2000条，截断会门禁复核。超过算量限制要求拆短用例，不偷偷抽稀后判定。JSON保留全分析曲线哈希、参数、原始指标、候选修复明细、`median_smoothed_samples`/`total_changed_samples`/`max_smoothing_change`及确认/质量证据。

### 5. 数字量非对称最小稳定时间

```json
"noise": {
  "rise_s": 0.003,
  "fall_s": 0.005,
  "max_rejected_edges": 24,
  "max_chatter_s": 0.025,
  "merge_gap_s": 0.006,
  "timing_guard": true
}
```

`rise_s`、`fall_s`、`max_chatter_s`必填且为非负秒；高/低电平分别需要达到最小持续时长才改变确认后的状态，不是最终脉冲宽度。`max_rejected_edges`默认20且为非负整数。`merge_gap_s`默认rise/fall较大者，合并间隔相近的被抑制段为抖动簇，检查最长簇。`timing_guard`默认true，将采样/确认证据纳入边沿/脉宽临界复核；它不改变输出边沿的起点时间戳。

审计原始边沿、确认边沿、被抑制段、最大簇时长以及事件起点/确认时间。分析左边界之外的边沿不会冒算成窗内抑制次数。尾部未完成确认会INCONCLUSIVE，不能借用窗口之后的样本。超出抑制计数/抖动簇时长预算也INCONCLUSIVE，避免把抖动风暴当作稳定电平。

`noise`与旧`debounce_s`互斥。数字脉冲的有效电平最小确认时间大于该用例声明的`min_width_s`时拒绝配置，以防抹掉合法保护脉冲。**这不是自动识别保护语义**：保护/跳闸通道仍应保留无去抖的原始断言。不能把接触器辅助接点参数批量套给所有数字量。

### 6. 模拟触发滞回和保持

在`analog_crossing`触发上增加`hysteresis`（非负工程单位）和`hold_s`（非负秒）。上升触发实际确认阈值为`threshold+hysteresis`，下降为`threshold-hysteresis`；必须先观察到对侧复位电平再布防。持续达到有效阈值满hold_s才确认，期间回到阈值内取消候选；只能用搜索终点前的样本确认。结果显示实际有效阈值与被拒绝候选数，不把有效阈值偷偷解释为中心阈值。

### 7. 配置审核建议

先冻结测量定义与原始硬限值，再根据采样率、仪表精度、已标注正常记录建立噪声基线。候选最长时间应明显短于必须识别的最短物理暂态；不知道时关闭替换/平滑并保留原始判定。门槛必须在待验收录波之前批准，不应反复调到PASS。

使用未参与定参的健康、故障、归因不明三类录波做盲测，分别统计误报、漏报及待复核比例。`N09`演示刻意采用max_noise_sigma=10、max_steady_drift=20，以单独验证持续振荡性能检查；并非程序观察到振荡后自动放宽噪声预算，也不是现场统一模板。

算法参考（仅方法，不提供验收阈值）：
1. MathWorks, Hampel identifier： https://www.mathworks.com/help/signal/ref/hampel.html
2. NIST, Median Absolute Deviation： https://www.itl.nist.gov/div898/software/dataplot/refman2/auxillar/mad.htm
3. NI, Analog Level Digital Filter Enable： https://www.ni.com/docs/en-US/bundle/ni-daqmx-labview-api-ref/page/resource/objmgr/daqmx-rc/daqmx-trigger/attr2ef0.html
检索日期2026-10-11；本软件使用独立标准库实现，不依赖上述商业软件。

# v1.3：文件夹批处理、跨通道关系与离线缩放

本节覆盖旧版关于“静态HTML不支持缩放”的描述；原有用例schema_version仍为1，无须改成1.3。目录扫描不会自动根据通道名称猜测用例。

## analog_relation：显式跨通道一致性

示例只是一项断言，放入完整用例的tests数组；p、pcs1、pcs2先在channels声明为模拟量，工程换算后单位必须完全相同。

```json
{
  "id": "P_BALANCE",
  "type": "analog_relation",
  "channel": "p",
  "window": [0.4, 5.7],
  "references": [
    {"channel": "pcs1", "gain": 1},
    {"channel": "pcs2", "gain": 1}
  ],
  "offset": 0,
  "tolerance": 0.4,
  "max_violation_s": 0.002,
  "hard_max_error": 1.0,
  "alignment_error_bound": 0
}
```

| 字段 | 定义 / 默认 |
|---|---|
| references | 必填1..16个参考，每项只有channel和可选gain（默认1）；不得重复，不能映射到同一个原始通道 |
| offset | 默认0；关系式的常数项，单位同映射后的被测量 |
| tolerance | 必填、非负；允许的绝对残差幅值，不是百分比 |
| max_violation_s | 必填、非负；累计绝对残差出带时长上限，单位秒；不是最长单次出带 |
| hard_max_error | 可选、非负；原始残差绝对峰值上限；不先去毛刺 |
| alignment_error_bound | 默认0、非负；项目预声明的绝对对齐/插值误差预算，与被测量同单位；不自动学习 |
| trigger、max_gap_s | 使用原有公共触发与间隙策略；window按触发相对秒解释 |

残差为 `被测通道 - (offset + Σ gain × reference)`。先按CFG模拟skew和显式工程换算确定各通道物理时间与值，取全部时间戳并集及窗口端点。在分段线性假设下求残差，并精确积分 `abs(residual)>tolerance` 的时间。参考通道上独有的采样尖峰也必须进入并集；不能只在被测通道原有采样点比较。并集上限200万点。

数据缺失/超允许间隙/窗口不足得到INCONCLUSIVE，不进行跨缺口插值。当前实现不自动优化时延，不支持跨录波时钟同步，也不证明两个通道具有独立计量溯源。虽然同一源通道会被拒绝，不同原始通道仍可能共享相同的传感器故障，必须由测试设计识别。

当B=alignment_error_bound非零：真实绝对残差的保守点值区间为 `[max(0,abs(r)-B), abs(r)+B]`。累计出带时长的确定下界用 `tolerance+B`，可能上界用 `tolerance-B`（负值时为全窗口）。峰值也建立上下界。上界满足限值才PASS，下界仍越限才FAIL，其余INCONCLUSIVE。此预算只在用户输入确有依据时有意义，不构成软件校准或统计置信区间。B=0表示接受采样重构值本身作为断言对象，不表示真实测量无误差。

参考和残差的摘要证据保存在JSON的reference_trace/residual_trace、reference_mappings中；当前可缩放主图展示被测通道，不声称已实现多参考残差交互叠加。

## 批处理配置冻结与配对

GUI提供同名JSON/统一JSON两种模式。统一模式在“扫描与检查配对”时深拷贝当前编辑器JSON，后续修改不能改变在途批次。上传采用大小核对和SHA-256；临时存储使用服务生成的opaque ID，不将用户文件路径作为写盘路径。

源清单保留浏览器webkitRelativePath；按完整相对目录+文件基名大小写折叠分组。路径包含反斜杠、绝对前缀、冒号、控制字符或 .. 被拒绝；普通中文和空格可用。扩展名大小写不敏感，同目录出现两个同类候选则ERROR。孤立DAT也产生错误记录，truth.json/manifest.json不作为待验收对象。

GUI和CLI的目录模式汇总真实验收状态，不读取truth.json或expected来反转结论。带完整预期的CLI manifest模式仍是明确的回归模式，不能用于把故障录波宣称合格。GUI取消只停止剩余组，不强制截断当前计算；取消/待执行不当PASS。关闭服务清理临时任务，不支持跨重启续跑。

CLI例：`python ESS_WaveTest.pyz batch --directory examples/field --recursive --sibling-cases --out my_batch`。

## 离线报告采样预算

HTML内嵌原始/处理采样点总预算600000，每项窗内点连同端点邻点计入；遇到缺口额外插入断线标记。报告放大先切原始内嵌数组，再做极值/数字边沿抽稀。预算不足或未提供源record时明确标summary；不能从summary恢复未保存的尖峰。工作台不受这个HTML嵌入预算影响，缩放继续读当前录波完整采样。

显示缩放不改变JSON配置、判定窗口、哈希或结果。历史报告的判定版本保留，交互查看器可独立更新。保存为PDF/PNG后只有静态图；交互需要下载HTML后在系统浏览器打开。
