# -*- coding: utf-8 -*-
"""immersive-scroll · 提取器（流水线第 1-3 段：探针 / 提取 / 压缩）

PPTX -> 中间产物（content.json + manifest.json + media_webp/），供 build_single_html.py 组装单文件页。

用法：
    python extract_pptx.py <input.pptx> <out_dir> [选项]

设计约定（对应 SKILL.md 第 1-3 段）：
  · 探针先行：页数 / 图片数 / 表格图表数 / 素材总体积 / 最大单图 / 有无占位符标题 -> probe_report.txt
  · 版面面积是判断图片主次的唯一可靠依据：area_pct = 图面积 / 页面积 × 100
  · 压缩按版面面积分级（阈值来自实测：≥15% -> 长边 1600，≥5% -> 1400，<5% -> 1100），只缩不放
  · 页脚角标（跨页高频 + 面积极小）自动排除，不当素材（实测：0.7% 且出现在 25/27 页）
  · 跨平台：不写任何本机路径；素材在内存中完成压缩，不落中间目录

中间产物格式（build_single_html.py 按此消费）：
  content.json  = {"media_sizes": {media: bytes}, "slides": [
                    {"slide": 1,
                     "paras": [{"text": ..., "sz": 14.0|None}],
                     "pics":  [{"name": ..., "media": "image1.png",
                                "geom": {"x","y","cx","cy"},          # EMU
                                "area_pct": 5.03, "in_group": false, "rank": 1}]}]}
  manifest.json = {"image1.png": {"file": "image1.webp", "bytes": 51980,
                                  "pages": [1], "max_area": 5.03}}
"""
import os
import sys
import io
import json
import argparse

from pptx import Presentation
from pptx.oxml.ns import qn
from pptx.enum.shapes import MSO_SHAPE_TYPE
from PIL import Image

# 压缩分级：与实测一致（area_pct 下界, 长边像素上限）。只缩不放，避免小图被硬拉大。
TIERS = ((15.0, 1600), (5.0, 1400), (0.0, 1100))
WEBP_QUALITY = 82


# ---------------------------------------------------------------- 探针工具

def _para_pt(para):
    """段落字号（pt）。取该段首个带字号设置的 run；都没有则 None（调用方按原文处理）。"""
    for run in para.runs:
        if run.font.size is not None:
            return round(run.font.size.pt, 1)
    return None


def _group_offset(grp):
    """组合内子元素坐标 -> 页面坐标的仿射参数 (ox, oy, sx, sy)。

    group 的子坐标系（chOff/chExt）与其自身占位（off/ext）常不一致，直接读子元素
    left/top 会得到偏移或缩放错误的位置。这里做标准换算；无缩放时返回恒等变换。
    """
    try:
        xfrm = grp._element.grpSpPr.xfrm
        off, ext = xfrm.off, xfrm.ext
        choff, chext = xfrm.chOff, xfrm.chExt
        if choff is None or chext is None or not chext.cx or not chext.cy:
            return 0, 0, 1.0, 1.0
        sx = float(ext.cx) / float(chext.cx)
        sy = float(ext.cy) / float(chext.cy)
        return (float(off.x) - float(choff.x) * sx,
                float(off.y) - float(choff.y) * sy, sx, sy)
    except Exception:
        return 0, 0, 1.0, 1.0


def _pic_media(shape):
    """图片 shape -> (media 名, blob)。media 名取包内真实文件名（如 image1.png）。"""
    rId = shape._element.blipFill.blip.get(qn('r:embed'))
    part = shape.part.related_part(rId)
    return os.path.basename(str(part.partname)), part.blob


def _walk(shapes, in_group, tf, out):
    """递归展开页面形状。group 内元素标记 in_group=True 并用换算后的页面坐标。"""
    for sh in shapes:
        try:
            stype = sh.shape_type
        except Exception:
            stype = None

        if stype == MSO_SHAPE_TYPE.GROUP:
            gtf = _group_offset(sh)
            _walk(sh.shapes, True, gtf, out)
            continue

        out.append((sh, in_group, tf))


def collect(slide, slide_w, slide_h, counters):
    """单页 -> (paras, pics_raw)。counters 累计表格/图表/OLE/占位符标题。"""
    items = []
    _walk(slide.shapes, False, (0, 0, 1.0, 1.0), items)

    paras = []
    pics = []
    for sh, in_group, tf in items:
        if getattr(sh, 'has_text_frame', False):
            for para in sh.text_frame.paragraphs:
                txt = para.text.strip()
                if txt:
                    paras.append({"text": txt, "sz": _para_pt(para)})

        if getattr(sh, 'has_table', False):
            counters['tables'] += 1
        if getattr(sh, 'has_chart', False):
            counters['charts'] += 1
        if sh.shape_type == MSO_SHAPE_TYPE.EMBEDDED_OLE_OBJECT:
            counters['ole'] += 1
        if getattr(sh, 'is_placeholder', False):
            try:
                if 'TITLE' in str(sh.placeholder_format.type):
                    counters['title_placeholders'] += 1
            except Exception:
                pass

        if sh.shape_type == MSO_SHAPE_TYPE.PICTURE:
            try:
                name, blob = _pic_media(sh)
            except Exception:
                continue
            ox, oy, sx, sy = tf
            x = int(float(sh.left) * sx + ox) if sh.left is not None else 0
            y = int(float(sh.top) * sy + oy) if sh.top is not None else 0
            cx = int(float(sh.width) * sx) if sh.width is not None else 0
            cy = int(float(sh.height) * sy) if sh.height is not None else 0
            area = (cx * cy) / float(slide_w * slide_h) * 100.0 if slide_w and slide_h else 0.0
            pics.append({"name": sh.name, "media": name, "blob": blob,
                         "geom": {"x": x, "y": y, "cx": cx, "cy": cy},
                         "area_pct": round(area, 2), "in_group": in_group})

    # 同页同一素材只留面积最大的一次（重复引用不产生额外素材条目）
    best = {}
    for p in pics:
        k = p["media"]
        if k not in best or p["area_pct"] > best[k]["area_pct"]:
            best[k] = p
    pics = sorted(best.values(), key=lambda p: -p["area_pct"])
    for i, p in enumerate(pics, 1):
        p["rank"] = i
    return paras, pics


# ---------------------------------------------------------------- 压缩

def tier_long_edge(area_pct):
    for lower, le in TIERS:
        if area_pct >= lower:
            return le
    return TIERS[-1][1]


def compress(blob, media_name, area_pct, out_webp_dir, counters, quality=WEBP_QUALITY):
    """单张素材 -> webp 文件名（失败返回 None）。只缩不放。"""
    tier = tier_long_edge(area_pct)
    try:
        with Image.open(io.BytesIO(blob)) as im:
            w, h = im.size
            scale = min(1.0, float(tier) / max(w, h))
            if scale < 1.0:
                im = im.resize((max(1, int(round(w * scale))), max(1, int(round(h * scale)))),
                               Image.LANCZOS)
            if im.mode not in ("RGB", "RGBA"):
                im = im.convert("RGBA" if 'A' in im.mode else "RGB")
            dst = os.path.join(out_webp_dir, os.path.splitext(media_name)[0] + ".webp")
            im.save(dst, "WEBP", quality=quality, method=5)
            counters['out_bytes'] += os.path.getsize(dst)
            counters['out_n'] += 1
            return os.path.basename(dst), (w, h), tuple(im.size), tier, im.mode
    except Exception as exc:
        counters['errors'].append("%s: %s" % (media_name, exc))
        return None


# ---------------------------------------------------------------- 主流程

def main(argv=None):
    ap = argparse.ArgumentParser(
        description="PPTX -> 中间产物（content.json / manifest.json / media_webp/）")
    ap.add_argument("pptx", help="输入 .pptx 路径")
    ap.add_argument("out_dir", help="中间产物输出目录（不存在则创建）")
    ap.add_argument("--quality", type=int, default=WEBP_QUALITY, help="WebP 质量，默认 82")
    ap.add_argument("--footer-page-ratio", type=float, default=0.5,
                    help="角标判定：出现页数 >= 总页数 × 该比例")
    ap.add_argument("--footer-max-area", type=float, default=1.0,
                    help="角标判定：最大版面面积（%%）小于该值")
    args = ap.parse_args(argv)

    pptx_path = os.path.abspath(args.pptx)
    out_dir = os.path.abspath(args.out_dir)
    webp_dir = os.path.join(out_dir, "media_webp")
    os.makedirs(webp_dir, exist_ok=True)

    report = []
    prs = Presentation(pptx_path)
    slide_w, slide_h = prs.slide_width, prs.slide_height
    n_slides = len(prs.slides)

    counters = {'tables': 0, 'charts': 0, 'ole': 0, 'title_placeholders': 0}
    slides = []
    media_blob = {}       # media -> blob（首次出现）
    media_pages = {}      # media -> [pages]
    media_area = {}       # media -> max area_pct

    for idx, slide in enumerate(prs.slides, 1):
        paras, pics = collect(slide, slide_w, slide_h, counters)
        for p in pics:
            media_blob.setdefault(p["media"], p["blob"])
            media_area[p["media"]] = max(media_area.get(p["media"], 0.0), p["area_pct"])
            media_pages.setdefault(p["media"], [])
            if idx not in media_pages[p["media"]]:
                media_pages[p["media"]].append(idx)
        slides.append({"slide": idx, "paras": paras,
                       "pics": [{k: v for k, v in p.items() if k != "blob"} for p in pics]})

    # ---- 探针报告（第 1 段）----
    raw_total = sum(len(b) for b in media_blob.values())
    biggest = max(media_blob.items(), key=lambda kv: len(kv[1])) if media_blob else ("-", b"")
    report.append("幻灯片: %d 页 | 尺寸: %.2f x %.2f in (EMU %d x %d)"
                  % (n_slides, slide_w / 914400.0, slide_h / 914400.0, slide_w, slide_h))
    report.append("素材: %d 个，合计 %.1f MB，最大单图 %s (%.2f MB)"
                  % (len(media_blob), raw_total / 1048576.0, biggest[0], len(biggest[1]) / 1048576.0))
    report.append("表格 %d / 图表 %d / OLE %d | 占位符标题 %d 个"
                  % (counters['tables'], counters['charts'], counters['ole'],
                     counters['title_placeholders']))
    report.append("文本段合计: %d（其中无字号 %d）"
                  % (sum(len(s["paras"]) for s in slides),
                     sum(1 for s in slides for p in s["paras"] if p["sz"] is None)))
    report.append("")
    report.append("判读提示：")
    report.append("  · 0 表格 0 图表 0 OLE -> 纯图文，转换无障碍；有图表需另议（矢量重建或截图）")
    report.append("  · 占位符标题 0 个 -> 抽取只得到无层级散文本，必须人工归类叙事章节（工作量主体）")
    if raw_total > 100 * 1048576:
        report.append("  · 素材 >100 MB -> 必须先压缩，否则内联 base64 产物浏览器打不开")
    report.append("")

    # ---- 提取（第 2 段）+ 压缩（第 3 段）----
    excluded = []
    for media in sorted(media_blob):
        pages = media_pages.get(media, [])
        max_area = media_area.get(media, 0.0)
        if n_slides and len(pages) >= max(2, int(n_slides * args.footer_page_ratio)) \
                and max_area < args.footer_max_area:
            excluded.append(media)
    excluded = set(excluded)

    cin = {'out_bytes': 0, 'out_n': 0, 'errors': []}
    manifest = {}
    media_sizes = {}
    report.append("--- 提取/压缩 ---")
    for media in sorted(media_blob, key=lambda m: (len(m), m)):
        blob = media_blob[media]
        media_sizes[media] = len(blob)
        if media in excluded:
            report.append("%-16s EXCLUDED(跨页高频角标: %d 页, 最大面积 %.1f%%)"
                          % (media, len(media_pages[media]), media_area[media]))
            continue
        got = compress(blob, media, media_area.get(media, 0.0), webp_dir, cin, args.quality)
        if got is None:
            continue
        webp_name, (ow, oh), (nw, nh), tier, mode = got
        manifest[media] = {"file": webp_name,
                           "bytes": os.path.getsize(os.path.join(webp_dir, webp_name)),
                           "pages": media_pages.get(media, []),
                           "max_area": media_area.get(media, 0.0)}
        report.append("%-16s %4dx%-4d -> %4dx%-4d %8.2f MB -> %7.2f MB  %s | le=%d area=%.1f%% pages=%s"
                      % (media, ow, oh, nw, nh, len(blob) / 1048576.0,
                         manifest[media]["bytes"] / 1048576.0, mode, tier,
                         media_area.get(media, 0.0), media_pages.get(media, [])))

    ratio = (sum(media_sizes.values()) / float(cin['out_bytes'])) if cin['out_bytes'] else 0.0
    report.append("")
    report.append("输入合计: %.1f MB (%d file) -> 输出合计: %.1f MB (%d file) | 压缩比 %.1f x"
                  % (sum(media_sizes.values()) / 1048576.0, len(media_sizes),
                     cin['out_bytes'] / 1048576.0, cin['out_n'], ratio))
    report.append("排除角标: %s" % (sorted(excluded) or "无"))
    if cin['errors']:
        report.append("压缩失败: %s" % cin['errors'])

    with io.open(os.path.join(out_dir, "content.json"), "w", encoding="utf-8") as f:
        f.write(json.dumps({"media_sizes": media_sizes, "slides": slides},
                           ensure_ascii=False, indent=1))
    with io.open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as f:
        f.write(json.dumps(manifest, ensure_ascii=False, indent=1))
    with io.open(os.path.join(out_dir, "probe_report.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(report))

    print("content.json / manifest.json / media_webp -> %s" % out_dir)
    print("素材 %d -> %d（排除角标 %d）| 压缩比 %.1f x"
          % (len(media_sizes), cin['out_n'], len(excluded), ratio))
    if cin['errors']:
        print("警告: %d 个素材压缩失败，详见 probe_report.txt" % len(cin['errors']))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
