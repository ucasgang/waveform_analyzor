# ESS WaveTest 1.2.0

**储能厂内 COMTRADE 自动化测试：毛刺、噪声、抖动处理 + 原始证据保护 + 可缩放双曲线。**

本包包括单文件程序、完整源码、中文工作台、67组生成录波（23基础 + 12复合 + 32噪声）、报告、自测及图文指南。工程验证版本，尚未通过客户现场验收。

## 立即开始

需要 **Python 3.10或以上**；程序运行、自测均只依赖Python标准库，不需要联网或安装第三方Python库。

Windows：解压到新目录，双击 `start.bat`。macOS / Linux：执行 `python3 ESS_WaveTest.pyz gui`。
浏览器地址为 `http://127.0.0.1:8765`；端口占用可加 `--port 8766`。源码入口为 `python launch.py gui`。
请勿用旧版pyz启动新目录。`python ESS_WaveTest.pyz --version`应输出 `1.2.0`。

在“录波与通道”下拉框选噪声案例，点击“载入演示”，再“运行全部断言”。推荐依次体验：

| 案例 | 应看到什么 |
|---|---|
| N01 白噪声 | 合理噪声背景下健康响应通过 |
| N02 EMI尖峰 | 原始超调30.81%，处理后0.60%，仍为待复核；不是滤干净就通过 |
| N11 原始安全尖峰 | 原始最大值124.4846 kW超过120 kW示例硬限，仍为FAIL |
| N15 接点抖动 | 原始8个边沿，确认后2个边沿，抑制6个且留痕 |
| N16 抖动风暴 | 160个原始边沿被抑制，但质量预算阻止把它判成稳定通过 |
| N17 真实保护脉冲 | 1 ms保护脉冲由原始断言捕获，禁止套用接点去抖 |
| N21 噪声模拟触发 | 滞回 + 持续保持后才确认触发 |

**上述全部限值均为教学工程配置，不是国标原文。** 示例种子固定为20261011。32组总体预期、33项业务断言及1项预期配置拒绝、独立公式核对见 `examples/noise/manifest.json` 与 `reports/noise_v12/summary.json`。

## 新版判定原则

模拟阶跃增加显式 `robust` 配置，数字断言增加显式 `noise` 配置；不配置则继续原有算法。模拟触发可配置 `hysteresis`、`hold_s`。

原始工程值和处理曲线同时保存。原始硬限值不经过滤波；原始峰值失败而处理后通过时进入INCONCLUSIVE。修复过多、噪声/漂移/削顶超过预先配置的质量预算、采样不足等，不自动提高降噪强度或放宽验收带。数字量保留被抑制边沿及确认延迟；危险的保护脉冲去抖组合会被拒绝。

Hampel候选检测、中值平滑不等于识别了物理噪声。仅凭一条波形不能可靠区分真实暂态和采集尖峰。抗噪分析是离线居中处理，不是在线保护算法；滤波后的时间并非无误差，附有工程复核保护带。完整原始分支随报告保留。

## 看原始与处理曲线

结果图可勾选“原始曲线”“处理曲线”“修复/抑制候选”；支持滚轮缩放、拖动平移、Shift框选、双击复位、输入起止秒、概览导航。N02定位 **0.825–0.840秒** 可看到两个被修复候选点，蓝色原始尖峰仍保留，橙色为处理值。

缩放读取局部完整采样；处理分支基于**整段断言窗口**计算并校验SHA-256，不在每次缩放时用局部视窗重新滤波。图层和缩放均不改变断言结论。未被配置抗噪的普通通道预览仍是原始波形。静态HTML报告包含双曲线和审计，但没有完整录波交互缩放。

## 配置与文档

在界面载入案例后可编辑用例JSON；本版尚无所有抗噪参数的可视化向导。先确认通道工程单位、采样分辨率、物理最短有效脉冲，再按项目审核参数。

- `docs/ESS_WaveTest_v1.2_抗噪算法与复杂波形验证.pdf`：新版图文指南；同名HTML可离线阅读。
- `docs/CONFIG_REFERENCE.md`：全部JSON字段；文末v1.2章节定义新增算法、单位、默认值和边界。
- `reports/噪声工况回归总览.html`：32组结果入口；须在解压目录打开，才能跟随相对报告链接。
- `docs/CHANGELOG_v1.2.md`：本次改动；`RELEASE.json`为机器可读发布信息。

v1.0的20页PDF/DOCX、v1.1的7页PDF/HTML保留为历史基础教程；旧版本测试数量、截图及验证声明不代表新版。新版数据以本README和当前JSON为准。

## 实际验证

| 验证层 | 结果 | 证据 |
|---|---|---|
| 标准库自测 | 756/756；0失败、0错误、0跳过 | reports/selftest_results.json |
| 噪声具名工况 | 32/32符合预期；33业务断言+1配置拒绝匹配 | reports/noise_v12/summary.json |
| 独立种子扰动 | 64种子×健康/真实超调/歧义尖峰=192次匹配，包含在756项内 | tests/test_noise_v12.py |
| 历史复合/基础工况 | 12组复合、23组基础预期保持 | reports/complex_audit.json / regression/summary.json |
| 新抗噪界面检查 | 29/29 | reports/browser_results_v12.json |
| 历史缩放界面检查 | 24/24 | reports/browser_results_v11.json |
| 单文件程序真实HTTP | 16/16 | reports/release_smoke.json |

验证层有重叠，不能相加成独立测试总数。故障应FAIL，数据不足/归因不明应INCONCLUSIVE，危险配置应ERROR；这些是回归正确，不是设备都通过。192次合成扰动不能解释成现场准确率或置信区间。

`reports/noise_adversarial_first.log`保留首轮专项失败记录。已修正窗口边缘抑制计数、ASCII生成样本与缺失值标记冲突，并补齐采样不足检查和幅值保护带；同时修复长案例名称导致390px窄屏溢出。不是将预期自动改成程序输出。

## 命令行与复现

```sh
python ESS_WaveTest.pyz --version
python ESS_WaveTest.pyz generate-noise --out regenerated_noise
python ESS_WaveTest.pyz run --cfg examples/noise/N02_emi_impulses.cfg --case examples/noise/N02_emi_impulses.json --out my_results
python ESS_WaveTest.pyz batch --manifest examples/noise/manifest.json --out noise_batch
python run_selftests.py
python tools/verify_noise.py
python tools/verify_complex.py
python tools/benchmark_noise.py
python tools/build_zipapp.py
python tools/release_smoke.py
```

`run`退出码0=PASS、1=FAIL、2=ERROR、3=INCONCLUSIVE。**N02退出3、N11退出1、N18退出2都是本案例的正确行为。** 带完整预期的manifest用于回归；逐断言和公式核对使用 `verify_noise.py`。重新自测/导出会更新报告文件，这是运行结果，不会改写原始CFG/DAT。

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
受受管浏览器策略限制，29项新界面及24项历史界面检查通过Chromium页面交互、Python转发到真实本机HTTP服务完成；没有更改管理策略，未完成浏览器直接导航localhost或操作系统级下载验证。16项单文件程序检查另以真实HTTP执行。

`reports/benchmark_noise.json`为新版同进程5次测量；N32包含16模拟+512数字、4001点，解析中位数约0.312秒，单个抗噪阶跃断言约0.031秒，不代表全部通道都做了同样分析。旧viewport_benchmark仅为历史基准。有限回归不能证明覆盖全部设备和工况。

## 二次开发

源码位于`esswave/`，解析、断言、报告、视窗数据和UI服务分离。配置JSON不执行Python表达式。
修改后执行`python run_selftests.py`，再`python tools/build_zipapp.py`重建单文件程序。
`tools/frontend_check_v12.py`和`tools/frontend_check_v11.py`为可选界面验证，需要另行准备Playwright和Chromium，可用`ESS_CHROMIUM`指定浏览器；不是应用运行依赖。

`tools/build_noise_guide.py`重建图文指南需要可选的WeasyPrint；与应用运行无关。本次不声明新版代码覆盖率百分比，93%仅为历史v1.0结果。

源文件和交付资料校验见`SHA256SUMS.txt`。软件许可见`LICENSE.txt`。
