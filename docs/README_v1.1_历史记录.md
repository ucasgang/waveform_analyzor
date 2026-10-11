# ESS WaveTest 1.1.0

**储能厂内 COMTRADE 自动化测试：复杂工况回归 + 可缩放波形时间轴。**

完整源码、单文件程序、中文工作台、35组生成录波、报告和教程均在本包内。
这是经过有限工程回归的离线验证版本，尚未用客户实际厂家录波完成现场验收；不等同于设备型式认证或标准一致性认证。

## 立即开始

需要 **Python 3.10或以上**。应用和标准库自测无需安装第三方Python库，运行不依赖互联网。

Windows：将软件包解压到新目录，双击 `start.bat`，保留命令行窗口。

macOS / Linux：在解压目录执行：

```sh
python3 ESS_WaveTest.pyz gui
```

浏览器默认打开本机 `http://127.0.0.1:8765`。端口占用时可用 `python ESS_WaveTest.pyz gui --port 8766`。
也可执行 `python launch.py gui` 从源码启动。不要用旧目录里的1.0程序启动新版界面。

先在“录波与通道”选择“正常响应”，点击“载入演示”，再“运行全部断言”，应得到6项通过。
再选择 **复杂C01**（18项通过）、**复杂C02**（两项故意失败）和 **复杂C09**（512通道与单点尖峰）。
C09的通道A128（测量_128）在1.2345秒有单采样125 V尖峰；定位1.234–1.236秒即可看清。
D300为密集变位，先全览，再定位1.234–1.237秒对照原始边沿。

## 时间轴怎么用

“录波与通道”的通道预览和“结果与证据”的波形图都支持：

| 操作 | 作用 |
|---|---|
| 滚轮 / ＋、－按钮 | 以光标时间 / 视窗中心为锚点缩放 |
| 按住左键拖动 | 平移时间视窗 |
| Shift + 左键拖动 | 框选时间段放大 |
| 双击 / 全览按钮 / Home键 | 复位到完整记录或完整证据窗口 |
| 输入开始、结束秒并点击“定位” | 精确查看时间区间 |
| 下方概览点击、拖动 / 左右箭头 | 快速定位 / 平移 |

**缩放会重新读取当前窗口的原始采样，不是放大旧抽稀图片。** 模拟量抽稀保留极值；数字量过密时明确显示状态包络，放大后恢复局部原始边沿。缺失点断线、采样间隙灰色提示。通道切换和快速缩放有旧请求隔离。

**缩放不修改用例、不改变断言窗口，也不重新解释通过/失败。** 分析始终使用完整采样。
结果图按对应断言的证据窗口和工程单位显示。静态导出HTML报告保存完整结果，但不附带全录波交互缩放；交互功能在本机工作台使用。

## 本次修复与新增测试

先对原版增加18项专项回归，复现13项失败，再修复6类问题：上升时间观察不足误判、去抖使用窗口外未来数据、边界模拟触发漏检、采样间隙旁触发错误可信、数值溢出未拒绝，以及显示跨缺失值连线。

新增12组复合工况，逐项核对163个业务断言，包含充电/放电/归零、多阶段握手、三相跌落与谐波背景、短时误跳、保护复归竞态、分段采样率与750 μs偏移、变比和单位换算、512通道单点尖峰、密集脉冲、目标漂移及选中通道缺失。

另外加入24组固定随机种子的正负一阶响应公式对照，以及24组高/低有效随机脉冲列对照。保留原有307项测试和23组录波。

| 验证层 | 实际结果 | 证据 |
|---|---|---|
| 标准库自动化测试 | **431/431**；0失败、0错误、0跳过 | `reports/selftest_results.json` |
| 新复合工况 | **12/12组、163/163项**匹配；另有5项关键指标区间对照 | `reports/complex_audit.json` |
| 原有录波回归 | **23/23组**符合预期 | `reports/regression/summary.json` |
| 浏览器界面交互 | **24/24** | `reports/browser_results_v11.json` |
| 单文件程序真实HTTP冒烟 | **10/10** | `reports/release_smoke.json` |

这些统计是不同验证层，**不可简单相加为独立测试总数**。12组复合工况的执行也在431项标准库测试中覆盖。
反例被判FAIL、证据不足被判INCONCLUSIVE，才是回归正确；“匹配预期”不表示全部设备合格。

`reports/v1_0_evidence/` 保存修改前的失败日志、复现结果及旧版证据。
本次未声明新版覆盖率百分比；该目录的93%记录仅属于1.0历史版本。

## 阅读顺序

`docs/ESS_WaveTest_v1.1_复杂工况验证与时间轴说明.pdf`：**本次7页图文说明**，有真实截图、复杂工况判读、修复说明及操作教程。同名HTML可离线阅读。

`reports/复杂工况回归总览.html`：12组真实执行结果、逐项判定和各详细报告入口；解压完整包后打开。

`docs/CONFIG_REFERENCE.md`：JSON配置字段、算法与v1.1边界语义。

`docs/ESS_WaveTest_使用说明与案例分析.pdf` / `.docx`：保留的**1.0版20页基础教程**，其旧界面截图、测试数量和验证声明仅作历史参考；新增操作与变更以本次说明为准。

`docs/CHANGELOG_v1.1.md`：修复明细与升级影响。`RELEASE.json`：机器可读交付信息。

## 命令行与复现

```sh
python ESS_WaveTest.pyz --version
python ESS_WaveTest.pyz inspect examples/complex/C09_dense_512.cfg
python ESS_WaveTest.pyz run --cfg examples/complex/C02_cycle_race.cfg --case examples/complex/C02_cycle_race.json --out my_results
python ESS_WaveTest.pyz batch --manifest examples/complex/manifest.json --out complex_results
python ESS_WaveTest.pyz batch --manifest examples/manifest.json --out basic_results
python ESS_WaveTest.pyz batch --directory recordings --case my_case.json --out batch_results
python ESS_WaveTest.pyz generate-complex --out regenerated_complex
python run_selftests.py
python tools/verify_complex.py
python tools/benchmark_viewport.py
python tools/release_smoke.py
```

`run`退出码：0通过、1失败、2配置/解析/执行错误、3数据不足或需复核。
带完整`expected`的清单进入预期结论回归模式，退出0表示各组总体状态匹配；更细的逐断言与独立指标核对由`tools/verify_complex.py`完成。
目录批处理是验收模式，各文件独立判定，不自动同步多组录波。
`run_selftests.py`会更新自测JSON；`verify_complex.py`会更新详细报告和审核JSON，静态总览HTML保留本次交付快照。

## 已有能力与限制

支持COMTRADE 1991、1999、2001、2013的受支持CFG/DAT子集；ASCII、BINARY、BINARY32、FLOAT32；UTF-8 / GB18030；a/b缩放、显式变比、skew、分段采样率、TIMEMULT和受限条件下按采样率重建时间。

通道按类型/名称/备选名/编号/通配符匹配，零匹配或多匹配不猜测。数字量有变位、完整高低有效脉冲、宽度、时延、保持和显式去抖。模拟量有范围、窗口RMS、正负阶跃、超调、响应/上升/调节时间、稳态误差与峰峰值。

结果为PASS / FAIL / INCONCLUSIVE / ERROR；HTML、JSON、CSV、JUnit包含配置快照、实际通道映射、窗口、测量值、限值和SHA-256。原始录波不被修改。

**所有随附限值均为合成工程示例，不是国标原文或已审核标准库。** 正式项目须审核标准/合同的对象、版次、条款与测量定义。`reviewed=true`只记录用户审核声明，不代表软件核实条款。

本版不提供CFF、厂家私有扩展、在线采集/设备控制、GOOSE/SV报文验证、SCL、完整并网认证、THD/频率/序分量/三相功率重建、跨录波同步、多用户权限或电子签章。三相跌落案例中的谐波只是输入工况，并非新增谐波检测功能。

浏览器上传上限128 MiB，CLI DAT上限512 MiB，CFG上限8 MiB，总通道上限4096，解码数组预算默认512 MiB（不是整个进程内存硬上限）。大文件优先CLI分批处理。

## 运行安全与验证边界

仅绑定127.0.0.1，检查Host、Origin及随机会话令牌。本机单用户工具，不应通过反向代理暴露到公网。SHA-256用于一致性比对，不是数字签名。

本次实测Linux / Python 3.13.5；未完成Windows、macOS实机验证，也没有客户真实厂家录波或现场验收基线。
受受管浏览器策略限制，24项界面检查通过Chromium页面交互、Python转发到真实本机HTTP服务完成；没有更改管理策略，没有完成浏览器直接导航localhost或操作系统级下载验证。10项单文件程序检查另以真实HTTP执行。

`reports/viewport_benchmark.json`为当前环境实测的解析与视窗数据生成时间，不含浏览器渲染/网络往返，也不保证目标工控机性能。有限回归不能证明涵盖全部设备和工况。

## 二次开发

源码位于`esswave/`，解析、断言、报告、视窗数据和UI服务分离。配置JSON不执行Python表达式。
修改后执行`python run_selftests.py`，再`python tools/build_zipapp.py`重建单文件程序。
`tools/frontend_check.py`为可选界面验证，需要另行准备Playwright和Chromium，可用`ESS_CHROMIUM`指定浏览器；不是应用运行依赖。

源文件和交付资料校验见`SHA256SUMS.txt`。软件许可见`LICENSE.txt`。
