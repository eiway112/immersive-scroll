# minimal example

一份最小但覆盖关键情形的样例，用来验证整条流水线真的能跑——不是只读文档。

样例 PPTX 由 `make_sample_pptx.py` 现场绘制（几何图形 + ASCII 标签），不含任何外部素材：
宽图 / 方图 / 竖图三种比例共存、一个组合（group）内的图片、一个表格、一个跨 8 页的
小角标（应被自动排除）。这些正是流水线上最容易出问题的地方。

## 四步跑通

```bash
# 1) 生成样例 PPTX（也可以换成你自己的 .pptx）
python make_sample_pptx.py

# 2) 探针 + 提取 + 压缩 -> content.json / manifest.json / media_webp/
python ../../scripts/extract_pptx.py sample.pptx ./work

# 3) 组装单文件 HTML
python ../../scripts/build_single_html.py --work ./work --sections sections.py --out ./out/demo.html

# 4) 双视口真机取证（桌面 1440×900 + 手机 390×844 + 禁用 JS）
node ../../scripts/verify_single_html.js ./out/demo.html --out ./verify_out
```

第 4 步会输出 `PASS / FAIL` 与截图，退出码 0 表示全部判据通过。

## 依赖

| 用途 | 依赖 |
|---|---|
| 第 1–3 步 | Python 3 + `python-pptx` + `Pillow` |
| 第 4 步 | Node.js + `playwright-core`（本机已有 playwright 的 chromium 缓存即可，不必重新下载浏览器） |

`playwright-core` 装到独立目录，别污染全局：

```bash
npm i playwright-core --prefix /path/to/isolated/workspace
NODE_PATH=/path/to/isolated/workspace/node_modules node ../../scripts/verify_single_html.js ...
```

## 你要改的地方只有一处

**`sections.py`** —— 它是"叙事重构"那一段。技能不替你决定章节怎么分，
因为那取决于内容语义，不取决于页码顺序（按页码等分切章是第 4 段明令禁止的做法）。

`sections.py` 与手写 HTML 同构，没有中间 DSL：

```python
def build_body(page):
    return f'''
    <section class="chapter" id="ch1">
      <div class="wrap">
        <h2>{page.T(2, "Overview")}</h2>          <!-- 按页号+前缀取原文 -->
        {page.fig("image2", "图注", "tall")}       <!-- 图片占位 -->
      </div>
    </section>'''
```

可用的三件工具：

| 调用 | 作用 |
|---|---|
| `page.T(页号, 前缀)` | 按页号 + 前缀取原文；取不到直接报错，避免图文错配 |
| `page.fig(key, 图注, 类名)` | 生成图片占位；类名含 `vs-img` 时自动走定值画框（比例失配的通用解法） |
| `specs_html([...])` | 参数行原文 → 参数表；按分号切字段，不做空格硬切（坑 4） |

图片 key 就是 `work/manifest.json` 里的素材名去掉扩展名（`image2.png` → `"image2"`）。

## 甲档 / 乙档

默认输出**甲档**（静态滚动叙事：淡入 + 章节导航 + 点击放大，零依赖）。

要**乙档**（GSAP 钉住擦洗、序列入场）需自备内联库并显式指定：

```bash
npm i gsap lenis --prefix /path/to/isolated/workspace
python ../../scripts/build_single_html.py --work ./work --sections sections.py \
    --out ./out/demo.html --vendor-dir /path/to/isolated/workspace/node_modules
```

本仓库**不附带** `gsap.min.js`——GSAP 的 Standard "No Charge" 许可不允许单独分发本体，
构建时从 npm 获取并内联即可（详见仓库根的 `LICENSE` 第三方声明）。

`sections.py` 顶部的 `USE_CUTAWAY` 控制是否输出乙档"构造穿越"段；设为 `False` 即为纯甲档。

## 取证判据

`verify_single_html.js` 无参数也跑通用判据：

- 双视口横向不可拖动（手机端三点位，以实际 `scrollLeft` 为准）
- 控制台 0 错误；内容图 `object-fit:cover` 为 0（装饰模糊底除外）
- **引用键 ↔ 内联数据差集为空**（双向；只比数量会漏掉"图 key 写错 → 该图永远空白"）
- **几何级：图片无拉伸变形**（`object-fit` 为 fill/none 且盒子比偏离素材比 >2% 即 FAIL）
- 关键图已加载；导航锚点有对应章节；点击放大取主体图 `img.fg`（坑 16）
- 甲档在手机端正确降级；**禁用 JS 正文可读且可见**（不只查文本长度）

项目专属判据用参数传入：

```bash
node ../../scripts/verify_single_html.js ./out/demo.html \
    --nowrap .nw --pin .cut-pin --shots "#ch3"
```

取证脚本内置三步自证（先证伪测量链路，再归因被测对象）：
滚动前销毁平滑滚动库 → 定位后等 ≥2.5s 且断言 `img.naturalWidth>0` → 截图文件名带时间戳前缀。
看不到预期效果时，先确认是页面坏了还是取证时机/工具错了。

> 本示例不自带 CSS，依赖构建器默认注入的版式层。想试验换皮：构建时加 `--no-pattern`
> 页面会回归原始文档流（机制判据仍全 PASS，但无卡片美化），再用自己的 `--css` 补版式。
