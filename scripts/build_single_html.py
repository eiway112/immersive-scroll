# -*- coding: utf-8 -*-
"""immersive-scroll · 构建器（流水线第 5-6 段：组装单文件 HTML + 静态自检）

把 extract_pptx.py 的中间产物 + 调用方写的段落编排，拼成一个**单文件自包含** HTML
（图片 base64 内联、库内联，双击即用、可离线、可直接转发）。

用法（命令行）：
    python build_single_html.py --work <中间产物目录> --sections <sections.py> --out <产物.html>
                                [--vendor-dir <内联库目录>] [--tour-js <漫游层.js>]
                                [--ar-marker vs-img] [--report <报告.txt>]

用法（作为模块，在 sections.py 里）：
    from build_single_html import Page
    page = Page(work_dir)                 # 读 content.json / manifest.json / media_webp
    page.fig("image3", "构造轴测图", "tall")   # 生成 <figure class="ph ..."><img data-img=...>
    page.T(2, "采用创新一体化")               # 按页号 + 前缀取原文（取不到直接报错，防空配）

边界（刻意不做的）：
  · 不做"章节自动编排"——按内容语义归章是叙事重构，判据是人的判断，不是页码等分。
    编排写在调用方的 sections.py 里，本模块只负责把编排结果装进单文件。
  · 不引配置 schema：调用方就是一段普通 Python，与手写 HTML 同构，没有中间 DSL。

三条硬约束（都是实测踩出来的，见 SKILL.md 坑 1/2/6）：
  1. 图片数据脚本必须排在主脚本之前，否则 window.__IMG__ 未定义 -> 全图不显示。
  2. 含外部源码的 __VENDOR__ 最后替换，避免其内部字面量被其他占位符二次替换；
     且 vendor 拼接后必须断言不含 `</script`。
  3. 占位符 key 带不带扩展名必须统一（本模块统一用不带扩展名的 key）。
"""
import os
import io
import re
import sys
import json
import math
import base64
import argparse


# ---------------------------------------------------------------- 纯文本工具

# 单行长度上限（字符）：微信内置浏览器（Android X5、iOS WKWebView）的解析器
# 对超长单行文本节点有实际上限，超限会截断或判定文档无效——**桌面 Chrome 无此限制**，
# 因此本问题在 PC 上测不出来，只在手机内置浏览器暴露（2026-09-21 实测）。
# 表现：手机内置浏览器打不开，Chrome 正常。
#
# 设两级阈值：
#   IMG_CHUNK_CHARS   单块目标值（多块拼装时的切分点，256 KB）
#   IMG_LINE_MAX      单行硬上限（**任何**一行都不得超过，64 KB）
# 单张图 base64 自身可能超过 256 KB，此时把该图字符串切片后跨行拼接，
# 保证「单行」始终 ≤ IMG_LINE_MAX——图片质量零损失（只改注入形式，不改数据）。
IMG_CHUNK_CHARS = 256 * 1024
IMG_LINE_MAX = 64 * 1024
# 块内单行载荷上限（留出引号/分号等壳字符余量）
IMG_PAYLOAD_MAX = IMG_LINE_MAX - 512


def _emit_image(key, value):
    """把一张图的赋值语句产出为「每行不超过 IMG_LINE_MAX」的形式。

    base64 本身不含引号、反斜杠等需转义字符，可安全按长度切片后用 `+` 拼接；
    JSON 转义后的外壳（双引号）只加在首尾，切片内容原样保留。
    """
    prefix = "window.__IMG__[" + json.dumps(key, ensure_ascii=False) + "]="
    tail = ";"
    # "data:image/webp;base64,XXXX" 的 JSON 串（含首尾引号）
    lit = json.dumps(value, ensure_ascii=False)
    if len(prefix) + len(lit) + len(tail) <= IMG_LINE_MAX:
        return [prefix + lit + tail]
    # 超长：拆成 (首段 + 若干中段 + 尾段) 的字符串拼接
    body = lit[1:-1]                      # 去掉外层引号
    avail = IMG_PAYLOAD_MAX - len(prefix) - 8   # 首行余量
    first, rest = body[:avail], body[avail:]
    lines = [prefix + '"' + first + '"']
    while rest:
        take, rest = rest[:IMG_PAYLOAD_MAX], rest[IMG_PAYLOAD_MAX:]
        lines.append('+"' + take + '"')
    lines[-1] += tail
    return lines


def imgdata_script(inline, chunk_chars=IMG_CHUNK_CHARS):
    """把内联图数据切成多个小 <script> 块，规避 WebView 超长单行限制。

    **为什么要分块（实测根因）**：94 张图序列化成一个 JSON 对象时产生 15.83 MB 的
    单行文本，微信内置浏览器直接打不开（Chrome 正常）。分块后单行 ≤64 KB。

    两级策略：
      1. 按 key 累加，超过 chunk_chars 起新块（块内是若干张图的赋值语句）；
      2. 单张图自身超长时，对该图字符串切片跨行拼接（见 _emit_image）。
    两条合起来保证 **任何单行都不超过 IMG_LINE_MAX**，与图片数量、单图大小都无关。

    所有块都排在主脚本之前，逐块挂到同一个 window.__IMG__ 上，
    主脚本读到的对象与单块注入时完全一致，渲染逻辑零改动。
    """
    if not inline:
        return "<script>window.__IMG__=window.__IMG__||{};</script>"

    # 先按 key 排序保证可重现（dict 顺序依赖插入序，跨次构建可能不同）
    items = sorted(inline.items())
    chunks, cur, cur_len = [], [], 0
    for k, v in items:
        seg_len = len(k) + len(v) + 64          # 64 = 语句壳的余量
        if cur and cur_len + seg_len > chunk_chars:
            chunks.append(cur)
            cur, cur_len = [], 0
        cur.append((k, v))
        cur_len += seg_len
    if cur:
        chunks.append(cur)

    parts = ["<script>window.__IMG__=window.__IMG__||{};</script>"]
    for c in chunks:
        lines = []
        for k, v in c:
            lines.extend(_emit_image(k, v))
        body = "\n".join(lines)
        # 硬闸门：任何一行超过 IMG_LINE_MAX 就等于分块失效（手机打不开）
        worst = max((len(l) for l in lines), default=0)
        if worst > IMG_LINE_MAX:
            raise AssertionError(
                "图片数据单行 %d 字符，超过硬上限 %d——分块失效，WebView 会打不开。"
                "（该块起始 key: %s）" % (worst, IMG_LINE_MAX, c[0][0]))
        parts.append("<script>" + body + "</script>")
    return "\n".join(parts)




def clean_label(text):
    """压缩中文标签内部的对齐空格（PPT 排版伪影）：'密   度：' -> '密度：'"""
    return re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", text).strip()


def spec_rows(text):
    """参数行 -> [[cell, ...], ...]（见 SKILL.md 坑 4）。

    先按分号切字段；段内多空格仅在两侧都含'：'时切两列，否则整段单列——
    '密   度：80kg/m3' 这类不会被空格拆断。
    """
    rows = []
    for seg in re.split(r"[;；]", text):
        seg = seg.strip()
        if not seg:
            continue
        cells = [x.strip() for x in re.split(r"\s{2,}", seg) if x.strip()]
        if len(cells) == 2 and "：" in cells[0] and "：" in cells[1]:
            rows.append([clean_label(cells[0]), clean_label(cells[1])])
        else:
            rows.append([clean_label(seg)])
    return rows


def specs_html(texts):
    """一组参数行原文 -> .spec-box 内部 HTML（见 SKILL.md 坑 4）。

    调用方先用 page.T() 取原文再传进来，本函数只管切分与排版。
    """
    out = []
    for text in texts:
        for row in spec_rows(text):
            if len(row) >= 2:
                out.append('<div class="spec-row"><span>%s</span><span>%s</span></div>'
                           % (row[0], row[1]))
            else:
                out.append('<div class="spec-row one"><span>%s</span></div>' % row[0])
    return "".join(out)


def geometric_mean(values):
    """几何平均。比例是乘性量，几何平均保证最竖/最横素材在定值画框内留白率对称（M6）。"""
    vs = [v for v in values if v and v > 0]
    if not vs:
        return 1.0
    return math.exp(sum(math.log(v) for v in vs) / len(vs))


# ---------------------------------------------------------------- 构建上下文

class Page(object):
    """单次构建的上下文：中间产物 + 素材表 + 引用记录。

    等价于一次性脚本里的模块级全局（IMG / SLIDES / used_keys），改成显式持有，
    这样同一进程可重复构建多个页面而不串味。
    """

    def __init__(self, work_dir, media_dir="media_webp"):
        self.work_dir = work_dir
        with io.open(os.path.join(work_dir, "content.json"), encoding="utf-8") as f:
            content = json.load(f)
        self.slides = {s["slide"]: s for s in content["slides"]}
        self.media_sizes = content.get("media_sizes", {})

        manifest_path = os.path.join(work_dir, "manifest.json")
        with io.open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)

        self.img = {}        # key -> data URI
        self.ratio = {}      # key -> 实测宽高比（供定值画框比例计算）
        try:
            from PIL import Image
        except ImportError:
            Image = None
        webp_dir = os.path.join(work_dir, media_dir)
        for media, info in manifest.items():
            path = os.path.join(webp_dir, info["file"])
            key = os.path.splitext(media)[0]
            if Image is not None:
                try:
                    with Image.open(path) as im:
                        w, h = im.size
                    if w and h:
                        self.ratio[key] = float(w) / float(h)
                except Exception:
                    pass
            with open(path, "rb") as f:
                self.img[key] = "data:image/webp;base64," + base64.b64encode(f.read()).decode("ascii")

    # ---- 文案取值（消费第 2 段的 content.json）----

    def T(self, slide, prefix, required=True):
        """按页号 + 前缀精确取原文；取不到直接报错，避免错配（见 SKILL.md 第 2 段）。

        正文一律取原文，不手抄、不改写；导航性 UI 短语允许重构，但术语必须忠于原文。
        """
        for para in self.slides[slide]["paras"]:
            if para["text"].startswith(prefix):
                return para["text"]
        if required:
            raise KeyError("P%s 未找到前缀: %s" % (slide, prefix))
        return ""

    def TS(self, slide, *prefixes):
        return [self.T(slide, p) for p in prefixes]

    # ---- 图片 ----

    def fig(self, key, caption="", cls="", alt="", ar_marker="vs-img"):
        """<figure class="ph ..."> + 懒注入 img。

        ar_marker 命中的类走"定值画框"结构：容器 aspect-ratio 统一 + 主体 contain +
        同图模糊底填充（复用同一 data-img 键，零体积增量）。比例失配素材的通用解法（M6）。
        """
        if key not in self.img:
            raise KeyError("图片不存在: %s" % key)
        cap = '<figcaption>%s</figcaption>' % caption if caption else ""
        if ar_marker and ar_marker in cls:
            inner = ('<img class="bg" data-img="%s" alt="" aria-hidden="true" loading="lazy">'
                     '<img class="fg" data-img="%s" alt="%s" loading="lazy">'
                     % (key, key, alt or caption))
        else:
            inner = '<img data-img="%s" alt="%s" loading="lazy">' % (key, alt or caption)
        return '<figure class="ph %s">%s%s</figure>' % (cls, inner, cap)

    def ar_for(self, html, marker="vs-img"):
        """按本页 HTML 中 marker 类素材的宽高比算画框比例（几何平均）。返回 (比例字符串, 样本数)。"""
        keys = []
        pat = r'<figure class="ph[^"]*\b%s\b[^"]*">(.*?)</figure>' % re.escape(marker)
        for m in re.finditer(pat, html, re.S):
            km = re.search(r'data-img="([^"]+)"', m.group(1))
            if km:
                keys.append(km.group(1))
        ratios = [self.ratio[k] for k in keys if k in self.ratio]
        if not ratios:
            return "1", 0
        return "%.3f" % geometric_mean(ratios), len(ratios)

    # ---- 组装 ----

    def build(self, out_path, title, body, css="", js="", nav_items=None,
              vendor_dir=None, vendor_files=None, tour_js="", extra_root_css="",
              ar_marker="vs-img", report_path=None, lang="zh-CN", pattern=True):
        """拼装并写出单文件 HTML，返回自检报告（同时写入 report_path）。

        pattern=True（默认）注入内置版式层 PATTERN_CSS；换皮时传 False 并自带 css。
        机制层 CORE_CSS 恒注入，不可关——关掉会丢掉防横向溢出/防变形/防 sticky 失效的约束。
        """
        body = body or ""
        nav_html = "\n".join('<a href="#%s"><span>%s</span></a>' % (i, t)
                             for i, t in (nav_items or []))

        html = TEMPLATE.replace("__LANG__", lang).replace("__TITLE__", title)

        # 替换顺序有讲究：含外部源码的 __VENDOR__ 必须最后（坑 2）。
        # 基础层固定在模板层注入，调用方传进来的 css 只作追加——否则调用方一个小疏忽
        # 就会把 html{overflow-x:clip} 这类防横向溢出的根级约束整层丢掉（实测踩过）。
        ar_value, ar_n = self.ar_for(body, ar_marker) if ar_marker else ("1", 0)
        blocks = [CORE_CSS]
        if pattern:
            blocks.append(PATTERN_CSS)
        if css:
            blocks.append(css)
        blocks.append(":root{--vs-ar:%s}" % ar_value)
        html = html.replace("__CSS__", "\n".join(blocks))
        html = html.replace("__NAV__", nav_html)
        html = html.replace("__BODY__", body)

        # 只内联被引用的图（引用不到的素材不进产物，避免白占体积）
        keys = set(re.findall(r'data-img="([^"]+)"', html))
        inline = {k: v for k, v in self.img.items() if k in keys}
        html = html.replace("__IMGDATA__", imgdata_script(inline))
        html = html.replace("__JS__", js or "")
        html = html.replace("__TOURJS__", tour_js or "")

        vendor = ""
        if vendor_dir:
            vendor = self.load_vendor(vendor_dir, vendor_files)
        html = html.replace("__VENDOR__", vendor)

        if extra_root_css:
            html = html.replace("</head>", "<style>%s</style>\n</head>" % extra_root_css)

        out_dir = os.path.dirname(os.path.abspath(out_path))
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        with io.open(out_path, "w", encoding="utf-8") as f:
            f.write(html)

        report = self.report(out_path, html, keys, vendor, ar_value, ar_n, tour_js, body)
        rp = report_path or os.path.join(out_dir or ".", "build_report.txt")
        with io.open(rp, "w", encoding="utf-8") as f:
            f.write("\n".join(report))
        return report

    @staticmethod
    def load_vendor(vendor_dir, files=None):
        """内联库源码。断言不含 `</script`——否则内联会截断整个 HTML（坑 2）。"""
        files = files or ("gsap.min.js", "ScrollTrigger.min.js", "lenis.min.js")
        parts = []
        for name in files:
            with io.open(os.path.join(vendor_dir, name), encoding="utf-8") as f:
                parts.append("/* ==== %s ==== */\n" % name + f.read())
        code = "\n".join(parts)
        if "</script" in code.lower():
            raise AssertionError("vendor 含 </script>，内联会截断 HTML：%s" % vendor_dir)
        return code

    def report(self, out_path, html, keys, vendor, ar_value, ar_n, tour_js, body):
        rep = []
        rep.append("输出: %s" % out_path)
        rep.append("大小: %.2f MB" % (os.path.getsize(out_path) / 1048576.0))
        rep.append("图片占位符(去重): %d / 内联: %d / 缺失: %s"
                   % (len(keys), len(self.img), sorted(keys - set(self.img)) or "无"))
        rep.append("未被引用: %s" % (sorted(set(self.img) - keys) or "无"))
        rep.append("残留占位符: %s" % ([p for p in re.findall(r"__[A-Z]{3,}__", html)
                                       if p != "__IMG__"] or "无"))
        # 坑 2 精化：内容图恒 0；装饰模糊底（class="bg"）允许 cover
        rep.append("cover 裁切: %d 处（其中装饰模糊底 %d 处 -> 内容图应为 0）"
                   % (html.count("object-fit:cover"), html.count('class="bg"')))
        rep.append("nowrap 保护: %d 处" % html.count('class="nw"'))
        rep.append("section 数: %d | 卡片数: %d | 图块数: %d"
                   % (len(re.findall(r"<section ", html)), html.count("<article"),
                      html.count('class="ph')))
        rep.append("vendor 内联: %.1f KB | 漫游层: %s"
                   % (len(vendor) / 1024.0, "已注入" if tour_js else "未注入"))
        rep.append("定值画框 --vs-ar=%s（来源 %d 张 %s 用图宽高比几何平均，实测像素）"
                   % (ar_value, ar_n, "画框" if ar_n else "无"))
        rep.append("正文长度: %d 字符" % len(body))
        return rep


# ---------------------------------------------------------------- 单文件模板

TEMPLATE = '''<!DOCTYPE html>
<html lang="__LANG__">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>__TITLE__</title>
<style>__CSS__</style>
<script>document.documentElement.className+=' js-fade';</script>
</head>
<body>
<div id="bar"></div>
<nav id="nav">__NAV__</nav>
<main class="page">
__BODY__
</main>
<div id="lb"><span class="close">&times;</span><img alt=""><div class="cap"></div></div>
<script>__VENDOR__</script>
__IMGDATA__
<script>__JS__</script>
<script>__TOURJS__</script>
</body>
</html>
'''

# ---------------------------------------------------------------- 命令行入口

def main(argv=None):
    ap = argparse.ArgumentParser(description="中间产物 + sections.py -> 单文件沉浸式 HTML")
    ap.add_argument("--work", required=True, help="extract_pptx.py 的输出目录")
    ap.add_argument("--sections", required=True, help="编排模块（需暴露 build_body(page)）")
    ap.add_argument("--out", required=True, help="产物 HTML 路径")
    ap.add_argument("--title", help="页面标题；缺省时读 sections.py 的 TITLE（没有则报错）")
    ap.add_argument("--css", help="追加 CSS 文件（与内置基础层合并）")
    ap.add_argument("--no-pattern", action="store_true",
                    help="不注入内置版式层（只留机制层），换皮时用 --css 自带全部版式")
    ap.add_argument("--vendor-dir", help="内联库目录（gsap.min.js / ScrollTrigger.min.js / lenis.min.js）")
    ap.add_argument("--tour-js", help="漫游层 JS 文件（乙档；缺省则不注入）")
    ap.add_argument("--ar-marker", default="vs-img", help="走定值画框的类名，默认 vs-img")
    ap.add_argument("--report", help="自检报告输出路径（默认与产物同目录 build_report.txt）")
    args = ap.parse_args(argv)

    sys.path.insert(0, os.path.dirname(os.path.abspath(args.sections)))
    mod_name = os.path.splitext(os.path.basename(args.sections))[0]
    sections = __import__(mod_name)

    page = Page(os.path.abspath(args.work))
    body = sections.build_body(page)
    nav_items = sections.nav_items(page) if hasattr(sections, "nav_items") else None

    # 标题来源优先级：命令行 > sections.py 的 TITLE > 报错。
    # 刻意不给默认值：标题会显示在微信/iMessage 的分享卡片上，默认值一旦生效
    # 就是"看起来构建成功、分享出去却是'沉浸式滚动页'"的静默事故（实测踩过）。
    title = args.title or getattr(sections, "TITLE", None)
    if not title:
        raise SystemExit(
            "缺少页面标题：命令行未传 --title，sections.py 里也没有 TITLE。\n"
            "标题会显示在分享卡片上，必须显式指定（不要依赖默认值）。")

    css = ""
    if args.css:
        with io.open(args.css, encoding="utf-8") as f:
            css = f.read()
    elif hasattr(sections, "CSS"):
        css = sections.CSS
    if hasattr(sections, "EXTRA_CSS") and sections.EXTRA_CSS:
        css = (css or "") + "\n" + sections.EXTRA_CSS

    tour_js = ""
    if args.tour_js:
        with io.open(args.tour_js, encoding="utf-8") as f:
            tour_js = f.read()
    elif hasattr(sections, "TOUR_JS"):
        tour_js = sections.TOUR_JS or ""

    for line in page.build(os.path.abspath(args.out), title, body, css=css,
                           js=BASE_JS + (getattr(sections, "EXTRA_JS", "") or ""),
                           nav_items=nav_items, vendor_dir=args.vendor_dir,
                           tour_js=tour_js, ar_marker=args.ar_marker,
                           report_path=args.report, pattern=not args.no_pattern):
        print(line)
    return 0


# ---------------------------------------------------------------- 基础层（甲档）

CORE_CSS = r'''
*{margin:0;padding:0;box-sizing:border-box}
:root{
  --bg:#08090f; --ink:#f4f1ea; --ink2:#98a2b8; --ink3:#5f6a80;
  --accent:#ff7a3d; --accent2:#4fd1c5; --line:rgba(255,255,255,.10);
  --panel:rgba(255,255,255,.04);
}
html{scroll-behavior:smooth}
/* 入场动画的初始位移会撑出横向可拖动区；约束留在 html 且用 clip（见坑 1/3/8） */
html{overflow-x:hidden;overflow-x:clip}
body{background:var(--bg);color:var(--ink);
  font-family:"PingFang SC","Microsoft YaHei","Hiragino Sans GB",system-ui,-apple-system,sans-serif;
  -webkit-font-smoothing:antialiased;line-height:1.75}
/* 注意：不要把 overflow-x 加到 body——它会让 body 成为 sticky 的滚动容器，
   自身 scrollTop 恒 0，导致全页 position:sticky 静默失效（坑 8）。 */
img{display:block;max-width:100%;height:auto}
.nw{white-space:nowrap}
.page{position:relative;z-index:1}
#bar{position:fixed;top:0;left:0;height:3px;width:0;z-index:60;
  background:linear-gradient(90deg,var(--accent),var(--accent2));transition:width .1s linear}
#nav{position:fixed;right:22px;top:50%;transform:translateY(-50%);z-index:60;
  display:flex;flex-direction:column;gap:14px}
#nav a{position:relative;width:9px;height:9px;border-radius:50%;
  background:rgba(255,255,255,.25);transition:.3s;text-decoration:none}
#nav a.on{background:var(--accent);box-shadow:0 0 0 4px rgba(255,122,61,.18)}
#nav a span{position:absolute;right:20px;top:50%;transform:translateY(-50%);
  white-space:nowrap;font-size:12px;color:var(--ink2);background:rgba(10,12,20,.9);
  padding:3px 9px;border-radius:6px;opacity:0;pointer-events:none;transition:.25s;border:1px solid var(--line)}
#nav a:hover span{opacity:1}
.ph{margin:0;position:relative;border-radius:14px;overflow:hidden;
  border:1px solid var(--line);background:rgba(255,255,255,.03)}
.ph img{width:100%;transition:transform .7s cubic-bezier(.2,.8,.2,1),opacity .6s}
.js-fade .ph img{opacity:0}
.js-fade .ph img.in{opacity:1}
.ph.zoomable{cursor:zoom-in}
.ph.zoomable:hover img{transform:scale(1.035)}
/* 图框内主体一律 contain——cover 会裁掉说明书类图纸的引线文字（坑 2）。
   这条属机制（防变形）故留 CORE；aspect-ratio/padding 等纯视觉部分在 PATTERN。 */
.card-img img{object-fit:contain}
/* 定值画框（M6）：不对齐内容，对齐"框"。
   --vs-ar 由构建器按该组素材宽高比的几何平均算出，非硬编码；
   bg = 同图放大模糊填充（装饰层，允许 cover），fg = 主体等比 contain（数学上保证不变形）。 */
.vs-img{position:relative;width:100%;aspect-ratio:var(--vs-ar,1);overflow:hidden;
  border-radius:12px;background:rgba(255,255,255,.03);border:1px solid var(--line)}
.vs-img img.bg{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;
  filter:blur(30px) saturate(1.05);opacity:.34;transform:scale(1.35)}
.vs-img img.fg{position:relative;display:block;width:100%;height:100%;object-fit:contain}
.vs-stage{display:grid;grid-template-columns:1fr auto 1fr;gap:22px;align-items:center}
.vs-side{display:flex;flex-wrap:wrap;gap:14px;align-items:flex-start}
.vs-side .ph{flex:1 1 45%}
.vs-mid{display:flex;align-items:center;justify-content:center}
.reveal{transition:opacity .9s cubic-bezier(.2,.8,.2,1),transform .9s cubic-bezier(.2,.8,.2,1)}
.js-fade .reveal{opacity:0;transform:translateY(34px)}
.js-fade .reveal.in{opacity:1;transform:none}
#lb{position:fixed;inset:0;z-index:100;background:rgba(4,5,10,.94);display:none;
  align-items:center;justify-content:center;padding:4vh 4vw}
#lb.on{display:flex}
#lb img{max-width:92vw;max-height:88vh;border-radius:10px;border:1px solid var(--line)}
#lb .close{position:absolute;top:22px;right:28px;font-size:30px;color:var(--ink2);cursor:pointer;
  line-height:1;user-select:none}
#lb .cap{position:absolute;bottom:26px;left:0;right:0;text-align:center;font-size:13px;color:var(--ink3)}
@media (max-width:960px){
  .vs-stage{grid-template-columns:minmax(0,1fr);gap:16px}
  .vs-stage>*{min-width:0;max-width:100%}
  .vs-mid{order:-1}
  #nav{display:none}
}
/* ===== 乙档漫游层：基础态 = 普通文档流；增强态只挂 html.tour（由漫游 JS 添加） ===== */
.cutaway{position:relative;padding:10vh 0 6vh}
.cut-stage{display:flex;flex-direction:column;gap:22px}
.cut-fig img{width:100%;max-height:72vh;object-fit:contain;background:rgba(0,0,0,.15)}
@media (min-width:961px){
  html.tour .cutaway{padding:0}
  html.tour .cut-pin{position:sticky;top:0;height:100vh;max-width:none;
    display:flex;flex-direction:column;justify-content:center;gap:2vh;overflow:hidden}
  html.tour .cut-head .sub{margin-bottom:1vh}
  html.tour .cut-fig{max-height:66vh}
  html.tour .cut-fig img{max-height:62vh}
}
@media (prefers-reduced-motion:reduce){
  *{animation:none!important;transition:none!important}
  .js-fade .reveal{opacity:1!important;transform:none!important}
  .js-fade .vs-side{opacity:1!important;transform:none!important}
  .js-fade .ph img{opacity:1!important}
}
'''

# 版式层：视觉风格（配色/字号/间距/栅格列数/圆角阴影/hover 动效），机制不依赖它。
# 默认注入以保证开箱可用；想彻底换皮用 --no-pattern 关掉，再用 --css 自带。
PATTERN_CSS = r'''
.wrap{max-width:1180px;margin:0 auto;padding:0 5vw}
.hero{min-height:100vh;display:flex;flex-direction:column;justify-content:center;align-items:center;
  text-align:center;padding:10vh 6vw;position:relative}
/* 封面 LOGO 压过通用图框样式时必须提高选择器优先级（.hero-logo.ph），
   否则会被样式表靠后的 .ph 规则覆盖——查源码看不出，得看 computed style（坑 1）。 */
.hero-logo.ph{position:absolute;top:max(22px,3.5vh);left:6vw;width:min(190px,40vw);margin:0;opacity:.96;
  border:none;background:transparent;border-radius:0;overflow:visible}
.hero-title{font-size:clamp(38px,7.5vw,92px);line-height:1.1;font-weight:700;letter-spacing:.02em}
.hero-title .l1{display:block;background:linear-gradient(100deg,#fff 20%,#ffb493 60%,var(--accent) 100%);
  -webkit-background-clip:text;background-clip:text;color:transparent}
.hero-title .l2{display:block;color:var(--ink)}
.hero-slogan{margin-top:20px;font-size:clamp(14px,2vw,20px);letter-spacing:.3em;
  color:var(--accent);font-weight:500}
.hero-lead{margin-top:24px;max-width:720px;font-size:clamp(14px,1.5vw,17px);color:#c9cfe0}
.hero-lead.dim{color:var(--ink2);margin-top:10px}
.hero-chips{margin-top:34px;display:flex;gap:12px;flex-wrap:wrap;justify-content:center}
.hero-chips span{font-size:13px;padding:7px 16px;border:1px solid var(--line);border-radius:999px;
  color:var(--ink2);background:var(--panel)}
.scroll-hint{position:absolute;bottom:40px;left:50%;transform:translateX(-50%);
  display:flex;flex-direction:column;align-items:center;gap:10px;color:var(--ink3);font-size:12px}
.mouse{width:22px;height:34px;border:1px solid rgba(255,255,255,.28);border-radius:12px;position:relative}
.mouse::after{content:"";position:absolute;left:50%;top:7px;width:3px;height:7px;border-radius:2px;
  background:var(--accent);transform:translateX(-50%);animation:wheel 1.8s infinite}
@keyframes wheel{0%{opacity:0;transform:translate(-50%,0)}30%{opacity:1}100%{opacity:0;transform:translate(-50%,12px)}}
.chapter{padding:14vh 0 12vh}
.eyebrow{font-size:13px;letter-spacing:.3em;color:var(--accent);margin-bottom:18px;
  display:flex;align-items:center;gap:12px}
.eyebrow i{font-style:normal;font-size:12px;padding:2px 9px;border:1px solid rgba(255,122,61,.4);
  border-radius:5px;color:var(--accent);letter-spacing:.1em}
.chapter h2{font-size:clamp(28px,5vw,58px);line-height:1.16;font-weight:700;margin-bottom:14px}
.sub{color:var(--ink2);font-size:clamp(14px,1.5vw,17px);max-width:760px;margin-bottom:52px}
.lead-grid{display:grid;grid-template-columns:1fr 1fr;gap:56px;align-items:center}
.lead .big{font-size:clamp(15px,1.6vw,19px);color:#dfe4f0}
.accent-line{margin-top:18px;color:var(--accent);font-size:clamp(15px,1.7vw,20px);font-weight:500;
  padding-left:16px;border-left:2px solid var(--accent)}
.tags{margin-top:26px;display:flex;gap:10px;flex-wrap:wrap}
.tags span{font-size:13px;padding:6px 15px;border-radius:999px;
  background:rgba(79,209,197,.10);border:1px solid rgba(79,209,197,.3);color:var(--accent2)}
figcaption{font-size:12.5px;color:var(--ink3);padding:11px 14px;border-top:1px solid var(--line);
  background:rgba(0,0,0,.25)}
.lead-fig .ph.tall{box-shadow:0 30px 80px rgba(0,0,0,.5)}
.card-img img{aspect-ratio:16/10;background:rgba(0,0,0,.22);padding:8px}
.cards{display:grid;gap:22px;margin-bottom:26px}
.cols-2{grid-template-columns:repeat(2,1fr)}
.cols-3{grid-template-columns:repeat(3,1fr)}
.card{background:var(--panel);border:1px solid var(--line);border-radius:18px;overflow:hidden;
  transition:transform .45s cubic-bezier(.2,.8,.2,1),border-color .45s,box-shadow .45s;
  display:flex;flex-direction:column}
.card:hover{transform:translateY(-7px);border-color:rgba(255,122,61,.45);
  box-shadow:0 22px 60px rgba(0,0,0,.45)}
.card-body{padding:24px 26px 28px}
.card h4{font-size:20px;margin-bottom:8px;font-weight:600}
.card .spec{font-size:13px;color:var(--accent);margin-bottom:14px;letter-spacing:.02em}
.card ul{list-style:none;display:flex;flex-direction:column;gap:9px}
.card li{font-size:14px;color:var(--ink2);padding-left:16px;position:relative;line-height:1.7}
.card li::before{content:"";position:absolute;left:0;top:11px;width:5px;height:5px;border-radius:50%;
  background:var(--accent);opacity:.9}
.card-gallery{display:grid;grid-template-columns:1fr;margin-top:18px}
.ph.mini{border-radius:9px}
.mat{display:grid;grid-template-columns:1.05fr .95fr;gap:44px;align-items:start;margin-bottom:64px;
  padding:38px;border:1px solid var(--line);border-radius:22px;background:var(--panel)}
.mat-text h3,.board-text h3{font-size:26px;margin-bottom:16px;color:var(--ink)}
.mat-text p{font-size:14.5px;color:var(--ink2);margin-bottom:12px}
.spec-box{margin-top:18px;border-top:1px solid var(--line);padding-top:14px}
.spec-row{display:grid;grid-template-columns:1fr 1fr;gap:18px;font-size:13.5px;
  padding:7px 0;color:#c3cad9;border-bottom:1px dashed rgba(255,255,255,.07)}
.spec-row.one{grid-template-columns:1fr}
.spec-row span:first-child{color:var(--accent2)}
.note{margin-top:14px;font-size:12.5px;color:var(--ink3)}
.mat-figs{display:grid;gap:16px;grid-template-columns:1fr 1fr;align-self:center;width:100%}
.mat-figs .ph:first-child{grid-column:1/-1}
.board{display:grid;grid-template-columns:.9fr 1.1fr;gap:40px;align-items:center;margin-bottom:52px;
  padding:32px;border:1px solid var(--line);border-radius:22px;background:var(--panel)}
.board.flip .board-fig{order:2}
.board-text p{font-size:14.5px;color:var(--ink2);margin-bottom:8px}
.gallery-block{margin-bottom:56px}
.gal-title{font-size:20px;margin-bottom:18px;color:var(--ink);font-weight:600}
.gallery{display:grid;gap:18px;align-items:start}
.gallery.cols-4{grid-template-columns:repeat(4,1fr)}
.gallery.cols-3{grid-template-columns:repeat(3,1fr)}
.gal-note{margin-top:14px;font-size:14px;color:var(--accent)}
.highlight{margin:10px 0 56px;padding:30px 34px;border-radius:18px;
  border:1px solid rgba(255,122,61,.35);background:rgba(255,122,61,.07)}
.highlight p{font-size:clamp(17px,2.4vw,26px);font-weight:600;color:#ffd0b8;line-height:1.5}
.vs-block{margin-bottom:64px}
.vs-head{display:flex;align-items:center;gap:14px;margin-bottom:22px}
.vs-tag{font-size:11.5px;letter-spacing:.18em;color:var(--ink3);border:1px solid var(--line);
  padding:3px 10px;border-radius:5px}
.vs-head h3{font-size:23px;font-weight:600}
.vs-side.left,.vs-side.right{transition:.9s cubic-bezier(.2,.8,.2,1)}
.js-fade .vs-side.left{transform:translateX(-40px);opacity:0}
.js-fade .vs-side.right{transform:translateX(40px);opacity:0}
.js-fade .vs-block.in .vs-side{transform:none;opacity:1}
.vs-mid span{font-size:15px;font-weight:700;letter-spacing:.1em;color:var(--accent);
  width:54px;height:54px;border-radius:50%;border:1px solid rgba(255,122,61,.5);
  display:flex;align-items:center;justify-content:center;background:rgba(255,122,61,.08)}
.vs-side.left .ph{border-color:rgba(79,209,197,.28)}
.vs-side.right .ph{border-color:rgba(255,122,61,.24)}
.vs-label{font-size:13.5px;padding:8px 14px;border-radius:8px;display:inline-block;margin-bottom:2px}
.vs-label.good{color:var(--accent2);background:rgba(79,209,197,.10);border:1px solid rgba(79,209,197,.28)}
.vs-label.bad{color:#ff9d7a;background:rgba(255,122,61,.10);border:1px solid rgba(255,122,61,.26)}
.vs-note{margin-top:16px;font-size:13px;color:var(--ink2);line-height:1.8;
  padding:14px 18px;border-left:2px solid var(--accent);background:rgba(255,255,255,.03);border-radius:0 10px 10px 0}
.policy-figs{display:grid;grid-template-columns:1fr 1fr;gap:16px;align-items:center}
.mod{display:grid;grid-template-columns:1fr 1fr;gap:44px;align-items:center;margin-bottom:56px;
  padding:34px;border:1px solid var(--line);border-radius:22px;background:var(--panel)}
.mod-text h3{font-size:24px;margin-bottom:12px}
.mod-lead{font-size:14.5px;color:var(--ink2);margin-bottom:20px}
.mod-group{margin-bottom:18px}
.mod-group h4{font-size:14px;color:var(--accent);letter-spacing:.06em;margin-bottom:8px;font-weight:600}
.mod-group ul{list-style:none;display:flex;flex-direction:column;gap:6px}
.mod-group li{font-size:13.8px;color:#c3cad9;padding-left:14px;position:relative}
.mod-group li::before{content:"";position:absolute;left:0;top:10px;width:4px;height:4px;
  border-radius:50%;background:var(--accent2)}
.outro{padding:16vh 0 0;text-align:center}
.outro-title{font-size:clamp(26px,4.5vw,52px);font-weight:700;margin-bottom:20px}
.outro-lead{max-width:720px;margin:0 auto;font-size:15px;color:var(--ink2)}
.outro-qr{margin:52px auto 0;display:flex;flex-direction:column;align-items:center;gap:10px}
.ph.qr{width:150px}
.qr-cap{font-size:13px;color:var(--ink3)}
.foot{margin-top:14vh;padding:26px 5vw;border-top:1px solid var(--line);
  display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap;
  font-size:12px;color:var(--ink3)}
@media (max-width:960px){
  .lead-grid,.mat,.board,.mod{grid-template-columns:1fr}
  .board.flip .board-fig{order:0}
  .cols-2,.cols-3{grid-template-columns:1fr}
  .gallery.cols-4{grid-template-columns:repeat(2,1fr)}
  .gallery.cols-3{grid-template-columns:1fr}
  .chapter{padding:10vh 0 8vh}
  .mat,.board,.mod{padding:24px}
}
.cut-pin{padding:0 5vw;max-width:1280px;margin:0 auto}
.cut-head .sub{margin-bottom:26px}
.cut-steps{list-style:none;display:grid;grid-template-columns:repeat(5,1fr);gap:12px}
.cut-steps li{border:1px solid var(--line);border-radius:12px;padding:12px 14px;
  background:var(--panel);transition:border-color .5s,background .5s,opacity .5s;opacity:.55}
.cut-steps li b{display:block;font-size:14.5px;color:var(--ink);margin-bottom:4px;font-weight:600}
.cut-steps li span{font-size:12px;color:var(--ink3);line-height:1.5;display:block}
.cut-steps li.on{opacity:1;border-color:rgba(255,122,61,.55);background:rgba(255,122,61,.08)}
.cut-steps li.on b{color:var(--accent)}
.vs-box .wrap{max-width:1280px}
.vs-flow{display:flex;flex-direction:column;gap:8px}
@media (max-width:960px){
  .cut-steps{grid-template-columns:repeat(2,1fr)}
}
'''

BASE_JS = r'''
(function(){
  'use strict';
  var IMG = window.__IMG__ || {};

  /* 图片懒注入：滚到视口附近才把 data URI 赋给 src。
     注意：data URI 已在页内，不存在网络请求，懒注入省的是解码与内存。 */
  var figures = document.querySelectorAll('.ph');
  for (var i=0;i<figures.length;i++){ figures[i].classList.add('zoomable'); }
  function loadImg(img){
    if (img.dataset.loaded) return;
    var k = img.getAttribute('data-img');
    if (k && IMG[k]) { img.src = IMG[k]; img.dataset.loaded = '1';
      img.addEventListener('load', function(){ img.classList.add('in'); }); }
  }
  if ('IntersectionObserver' in window){
    var io = new IntersectionObserver(function(entries){
      entries.forEach(function(e){
        if (e.isIntersecting){ loadImg(e.target); io.unobserve(e.target); }
      });
    }, {rootMargin:'400px 0px'});
    document.querySelectorAll('.ph img[data-img]').forEach(function(im){ io.observe(im); });
  } else {
    document.querySelectorAll('.ph img[data-img]').forEach(loadImg);
  }

  /* 滚动淡入 */
  var revealEls = document.querySelectorAll('.reveal, .vs-block');
  if ('IntersectionObserver' in window){
    var io2 = new IntersectionObserver(function(entries){
      entries.forEach(function(e){ if (e.isIntersecting){ e.target.classList.add('in'); } });
    }, {rootMargin:'0px 0px -12% 0px', threshold:0.08});
    revealEls.forEach(function(el){ io2.observe(el); });
  } else {
    revealEls.forEach(function(el){ el.classList.add('in'); });
  }

  /* 进度条 + 侧边导航高亮 */
  var bar = document.getElementById('bar');
  var secs = [].slice.call(document.querySelectorAll('section[id]'));
  var links = [].slice.call(document.querySelectorAll('#nav a'));
  function onScroll(){
    var h = document.documentElement;
    var max = h.scrollHeight - window.innerHeight;
    var p = max > 0 ? Math.min(1, Math.max(0, (h.scrollTop || document.body.scrollTop)) / max) : 0;
    if (bar) bar.style.width = (p * 100) + '%';
    var cur = 0;
    for (var i=0;i<secs.length;i++){
      if (secs[i].getBoundingClientRect().top <= window.innerHeight * 0.4) cur = i;
    }
    links.forEach(function(a, idx){ a.classList.toggle('on', idx === cur); });
  }
  window.addEventListener('scroll', onScroll, {passive:true});
  onScroll();

  /* 点击放大 */
  var lb = document.getElementById('lb');
  var lbImg = lb ? lb.querySelector('img') : null;
  var lbCap = lb ? lb.querySelector('.cap') : null;
  if (lb){
    document.addEventListener('click', function(ev){
      var ph = ev.target.closest ? ev.target.closest('.ph.zoomable') : null;
      if (ph){
        var im = ph.querySelector('img.fg') || ph.querySelector('img');
        if (im && im.src){ lbImg.src = im.src;
          var cap = ph.querySelector('figcaption');
          lbCap.textContent = cap ? cap.textContent : (im.getAttribute('alt') || '');
          lb.classList.add('on'); }
        return;
      }
      if (ev.target === lb || ev.target.classList.contains('close')) lb.classList.remove('on');
    });
    document.addEventListener('keydown', function(e){ if (e.key === 'Escape') lb.classList.remove('on'); });
  }
})();
'''

# 乙档漫游层：三条纪律（缺一即踩坑）
#   1. 全部 pin/scrub 包进 gsap.matchMedia('(min-width: 961px)')，移动端保持原生滚动
#      （微信内置浏览器对 sticky/100vh 不稳，长钉住段手机体验差）；
#   2. 横向劫持滚轮永久不做——输入方向与视觉方向必须一致（坑 11）；
#   3. 返回 cleanup：移除 html.tour、还原 inline height、销毁 lenis。
TOUR_JS = r'''
(function(){
'use strict';
if (!(window.gsap && window.ScrollTrigger)) return;
if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
gsap.registerPlugin(ScrollTrigger);
var mm = gsap.matchMedia();

mm.add('(min-width: 961px)', function(){
  var root = document.documentElement;
  root.classList.add('tour');

  var lenis = null, tickFn = null;
  if (window.Lenis){
    lenis = new Lenis({ duration: 1.1, smoothWheel: true });
    lenis.on('scroll', ScrollTrigger.update);
    tickFn = function(t){ lenis.raf(t * 1000); };
    gsap.ticker.add(tickFn);
    gsap.ticker.lagSmoothing(0);
    window.__lenis = lenis;
    /* 锚点导航走平滑滚动；无 Lenis 时回退原生跳转 */
    document.querySelectorAll('#nav a[href^="#"]').forEach(function(a){
      a.addEventListener('click', function(ev){
        var L = window.__lenis, t = document.querySelector(a.getAttribute('href'));
        if (L && t){ ev.preventDefault(); L.scrollTo(t); }
      });
    });
  }

  /* 锚点①：构造穿越——滚动即剖切（clip-path 从左向右逐层揭示）
     锚点可改名：优先 [data-tour-cutaway]，其次 #cutaway（示例编排用的 id）。
     两者都没有时明确告警——静默失效（不报错、整段漫游不生效）比报错难查得多。 */
  var cut = document.querySelector('[data-tour-cutaway]') || document.getElementById('cutaway');
  if (!cut && window.console) {
    console.warn('[tour] 未找到穿越锚点：给章节加 id="cutaway" 或 data-tour-cutaway，否则本段漫游不生效');
  }
  var cutFig = cut ? cut.querySelector('.cut-fig') : null;
  var cutSteps = cut ? cut.querySelectorAll('.cut-steps li') : [];
  var setCutH = null;
  if (cut && cutFig){
    setCutH = function(){ cut.style.height = Math.round(window.innerHeight * 2.6) + 'px'; };
    setCutH();
    gsap.set(cutFig, { clipPath: 'inset(0 68% 0 0)' });
    gsap.timeline({ scrollTrigger: {
      trigger: cut, start: 'top top', end: 'bottom bottom', scrub: 0.5,
      onUpdate: function(self){
        var n = cutSteps.length || 1;
        for (var i = 0; i < cutSteps.length; i++){
          cutSteps[i].classList.toggle('on', self.progress >= (i + 0.6) / n * 0.92);
        }
      }
    }}).to(cutFig, { clipPath: 'inset(0 0% 0 0)', ease: 'none', duration: 1 });
    ScrollTrigger.addEventListener('refreshInit', setCutH);
  }

  /* 锚点②：卡片群序列入场（batch stagger） */
  gsap.set('.cards .card', { opacity: 0, y: 26 });
  ScrollTrigger.batch('.cards .card', { start: 'top 90%', once: true,
    onEnter: function(batch){
      gsap.to(batch, { opacity: 1, y: 0, duration: .7, stagger: .08, ease: 'power2.out', overwrite: true });
    } });

  return function(){
    root.classList.remove('tour');
    if (cut) cut.style.height = '';
    if (setCutH) ScrollTrigger.removeEventListener('refreshInit', setCutH);
    if (lenis){ gsap.ticker.remove(tickFn); lenis.destroy(); window.__lenis = null; }
  };
});
})();
'''


if __name__ == "__main__":
    sys.exit(main())
