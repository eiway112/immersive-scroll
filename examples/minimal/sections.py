# -*- coding: utf-8 -*-
"""examples/minimal · 示例编排（流水线第 4 段：叙事重构）

这一段技能不替你决定——按内容语义归章是人的判断，不是页码等分。
你在这里写的就是这个项目的章节结构，形式与手写 HTML 同构：
拿 page.fig() / page.T() 拼字符串，没有中间 DSL、没有配置 schema。

  · page.T(页号, 前缀)  按页号 + 前缀取原文（取不到直接报错，防错配）
  · page.fig(key, 说明, 类名)  图片占位；类名含 "vs-img" 时自动走定值画框
  · specs_html([...])   参数行原文 -> .spec-box 排版（按分号切字段，见坑 4）

跑法见同目录 README.md。
"""
from build_single_html import specs_html, TOUR_JS as _TOUR_JS

# 是否输出乙档"构造穿越"段。切到 False 可看纯甲档（静态滚动叙事）。
USE_CUTAWAY = True

# 乙档漫游层：构建器内置模板；甲档下为空串（不注入）
TOUR_JS = _TOUR_JS if USE_CUTAWAY else ""


def nav_items(page):
    items = [("hero", "首页"), ("ch1", "概述"), ("ch2", "规格卡"), ("ch3", "工艺对比")]
    if USE_CUTAWAY:
        items.append(("cutaway", "构造穿越"))
    items.append(("outro", "结尾"))
    return items


def build_body(page):
    parts = []

    # ---------------- 封面 ----------------
    parts.append(f'''
<section class="hero" id="hero">
  <div class="hero-inner">
    <h1 class="hero-title"><span class="l1">{page.T(1, "Sample")}</span></h1>
    <p class="hero-slogan">{page.T(1, "Minimal fixture")}</p>
    <div class="hero-chips"><span>extract</span><span>build</span><span>verify</span></div>
  </div>
  <div class="scroll-hint"><span class="mouse"></span><em>向下滚动</em></div>
</section>
''')

    # ---------------- 01 概述（左文右图） ----------------
    parts.append(f'''
<section class="chapter" id="ch1" data-nav="概述">
  <div class="wrap">
    <div class="eyebrow reveal"><i>01</i> {page.T(2, "Overview")}</div>
    <h2 class="reveal">正文按页取原文<br>不手抄、不改写</h2>
    <div class="lead-grid">
      <div class="lead reveal">
        <p class="big">{page.T(2, "Body copy taken verbatim")}</p>
        <div class="tags"><span>双视口取证</span><span>判据分三层</span></div>
      </div>
      <div class="lead-fig reveal">{page.fig("image2", "方形素材（1:1）", "tall")}</div>
    </div>
  </div>
</section>
''')

    # ---------------- 02 规格卡（参数行按分号切字段） ----------------
    specs = specs_html([page.T(3, "Spec: 120"), page.T(3, "Standard length:")])
    bullets = "".join("<li>%s</li>" % page.T(3, b) for b in
                      ("Bullet one:", "Bullet two:", "Bullet three:"))
    parts.append(f'''
<section class="chapter" id="ch2" data-nav="规格卡">
  <div class="wrap">
    <div class="eyebrow reveal"><i>02</i> {page.T(3, "Spec Card")}</div>
    <h2 class="reveal">参数行不做空格硬切</h2>
    <p class="sub reveal">按分号切字段、双冒号校验才分列——避免"密 度："被空格拆断（坑 4）</p>
    <div class="cards cols-2">
      <article class="card reveal">
        {page.fig("image3", "", "card-img")}
        <div class="card-body">
          <h4>Spec Card</h4>
          <div class="spec">竖图素材（1:2）</div>
          <ul>{bullets}</ul>
        </div>
      </article>
      <article class="card reveal">
        <div class="card-body">
          <h4>参数表</h4>
          <div class="spec-box">{specs}</div>
        </div>
      </article>
    </div>
  </div>
</section>
''')

    # ---------------- 03 工艺对比（定值画框：不对齐内容，对齐"框"） ----------------
    parts.append(f'''
<section class="chapter" id="ch3" data-nav="工艺对比">
  <div class="wrap">
    <div class="eyebrow reveal"><i>03</i> {page.T(4, "Comparison")}</div>
    <h2 class="reveal">比例失配是常态</h2>
    <p class="sub reveal">16:9 与 9:16 同框：容器统一 aspect-ratio + 主体 contain + 同图模糊底</p>
    <div class="vs-block reveal">
      <div class="vs-head"><span class="vs-tag">对比</span><h3>定值画框</h3></div>
      <div class="vs-stage">
        <div class="vs-side left">{page.fig("image4", "", "vs-img")}</div>
        <div class="vs-mid"><span>VS</span></div>
        <div class="vs-side right">{page.fig("image5", "", "vs-img")}</div>
      </div>
      <p class="vs-note">--vs-ar 由构建器按该组素材宽高比的几何平均算出，不是硬编码。</p>
    </div>
  </div>
</section>
''')

    # ---------------- 04 构造穿越（乙档锚点①） ----------------
    if USE_CUTAWAY:
        steps = [("01", "素材层", "宽图 3:1"),
                 ("02", "框层", "aspect-ratio 统一"),
                 ("03", "主体层", "object-fit:contain"),
                 ("04", "装饰层", "同图模糊底填充"),
                 ("05", "校验层", "变形率 >2% 即 FAIL")]
        steps_html = "".join('<li data-step="%s"><b>%s %s</b><span>%s</span></li>' % (n, n, t, d)
                             for n, t, d in steps)
        parts.append(f'''
<section class="cutaway" id="cutaway" data-nav="构造穿越">
  <div class="cut-pin">
    <div class="cut-head">
      <div class="eyebrow reveal"><i>04</i> 滚动即剖切</div>
      <h2 class="reveal">把"框"逐层拆开看</h2>
      <p class="sub reveal">clip-path 随滚动从左向右揭示 · 桌面端钉住擦洗，移动端自动降级为普通滚动</p>
    </div>
    <div class="cut-stage">
      {page.fig("image1", "", "cut-fig")}
      <ol class="cut-steps">{steps_html}</ol>
    </div>
  </div>
</section>
''')

    # ---------------- 收尾 ----------------
    parts.append(f'''
<section class="outro" id="outro" data-nav="结尾">
  <div class="wrap outro-inner">
    <div class="reveal">
      <h2 class="outro-title">{page.T(6, "Table Page")}</h2>
      <p class="outro-lead">{page.T(6, "Tables must be reported")}</p>
    </div>
  </div>
  <footer class="foot"><span>immersive-scroll · minimal example</span>
    <span>本页由样例 PPTX 端到端生成，供流水线冒烟验证</span></footer>
</section>
''')

    return "\n".join(parts)
