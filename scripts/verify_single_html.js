// immersive-scroll · 取证器（流水线第 7 段：双视口真机取证）
//
// 静态验证 PASS 不等于页面正确——本脚本用真实浏览器跑判据，且必须双视口：
//   Ａ 桌面 1440×900（漫游层应启用）  Ｂ 手机 390×844（漫游层应降级）  Ｃ 禁用 JS（内容应可读）
//
// 用法：
//   node verify_single_html.js <产物.html> [--chrome <chromium路径>] [--out <输出目录>]
//        [--nowrap <选择器>]        不拆行判据（如 --nowrap .nw）
//        [--pin <选择器>]           钉住判据：粘住区间内该元素 top 应为 0（如 --pin .cut-pin）
//        [--shots <选择器,选择器>]  额外定点截图
//        [--no-shots]               不截图（只要报告）
//
// 通用判据（无参数也跑）：横向不可拖动 / 控制台 0 错误 / 内容图非 cover /
//                        图片引用数=内联数 / 关键图已加载 / 甲档降级正确 / 禁 JS 可读
//
// 取证三步自证（M3：先证伪测量链路，再归因被测对象）
//   ① 滚动前销毁平滑滚动库——Lenis 的 raf 会把原生 scrollTo 的位置写回自己的插值目标
//   ② 定位后等 >=2.5s 且断言 img.naturalWidth>0——瞬跳长距离后立刻截图会拍到黑屏（是时机错，不是页面坏）
//   ③ 截图文件名带头部时间戳前缀——防旧同名文件与记忆互相印证出错误结论

'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');

let chromium;
try {
  chromium = require('playwright-core').chromium;
} catch (e) {
  console.error('[FATAL] 需要 playwright-core。安装：npm i playwright-core（装到独立目录，别污染全局）');
  console.error('        已缓存 chromium 时无需重新下载浏览器，本脚本会自动探测其可执行文件。');
  process.exit(2);
}

const argv = process.argv.slice(2);
const HTML = argv[0] && !argv[0].startsWith('--') ? argv[0] : null;
function opt(name, dflt) {
  const i = argv.indexOf('--' + name);
  return i >= 0 && argv[i + 1] && !argv[i + 1].startsWith('--') ? argv[i + 1] : dflt;
}
function flag(name) { return argv.indexOf('--' + name) >= 0; }

if (!HTML) {
  console.error('用法: node verify_single_html.js <产物.html> [--chrome 路径] [--out 目录] [--nowrap 选择器] [--pin 选择器]');
  process.exit(2);
}

const STAMP = new Date().toISOString().replace(/[-:T]/g, '').slice(0, 14);
const OUTDIR = path.resolve(opt('out', path.join(os.tmpdir(), 'immersive-verify-' + STAMP)));
const NOWRAP = opt('nowrap', null);
const PIN = opt('pin', null);
const EXTRA_SHOTS = (opt('shots', '') || '').split(',').map(s => s.trim()).filter(Boolean);
const WANT_SHOTS = !flag('no-shots');
const DESKTOP = (opt('desktop', '1440x900')).split('x').map(Number);
const MOBILE = (opt('mobile', '390x844')).split('x').map(Number);

const lines = [];
let fails = 0;
function log(s) { lines.push(s); console.log(s); }
function check(ok, label, detail) {
  if (!ok) fails++;
  log((ok ? '  PASS  ' : '  FAIL  ') + label + (detail ? '  :: ' + detail : ''));
}

// ---------------------------------------------------------------- chromium 探测
// 按 playwright 官方缓存路径枚举本机 chromium（不必重新下载浏览器），取版本号最大者。
function pickChromium() {
  const rels = [];
  if (process.platform === 'win32') {
    rels.push([path.join(process.env.LOCALAPPDATA || '', 'ms-playwright'),
      ['chrome-win64', 'chrome.exe'], ['chrome-win', 'chrome.exe']]);
  } else if (process.platform === 'darwin') {
    rels.push([path.join(os.homedir(), 'Library', 'Caches', 'ms-playwright'),
      ['chrome-mac64', 'Chromium.app', 'Contents', 'MacOS', 'Chromium'],
      ['chrome-mac', 'Chromium.app', 'Contents', 'MacOS', 'Chromium']]);
  } else {
    rels.push([path.join(os.homedir(), '.cache', 'ms-playwright'),
      ['chrome-linux', 'chrome'], ['chrome-linux64', 'chrome']]);
  }
  const found = [];
  for (const [dir, ...cands] of rels) {
    if (!fs.existsSync(dir)) continue;
    for (const name of fs.readdirSync(dir)) {
      if (!name.startsWith('chromium-')) continue;      // 排除 chromium_headless_shell-*
      const ver = parseInt(name.split('-')[1], 10) || 0;
      for (const c of cands) {
        const p = path.join(dir, name, ...c);
        if (fs.existsSync(p)) { found.push({ p, ver }); break; }
      }
    }
  }
  found.sort((a, b) => b.ver - a.ver);
  return found.length ? found[0].p : null;
}

const CHROME = opt('chrome', pickChromium());
if (!CHROME || !fs.existsSync(CHROME)) {
  console.error('[FATAL] 未找到 chromium 可执行文件。用 --chrome 指定，或先装 playwright 的 chromium。');
  process.exit(2);
}

const fileUrl = 'file:///' + path.resolve(HTML).replace(/\\/g, '/');

// ---------------------------------------------------------------- 页面通用探针
// M3①：销毁平滑滚动库，否则原生 scrollTo 会被其 raf 循环写回
const KILL_SMOOTH = `(() => {
  try { if (window.__lenis) { window.__lenis.destroy(); window.__lenis = null; } } catch (e) {}
  const s = document.documentElement.style;
  s.scrollBehavior = 'auto';
})()`;

async function settle(page, ms) {
  await page.waitForTimeout(ms || 2500);
  // M3②：断言图片真的加载出来了，再截图
  return page.evaluate(() => {
    const imgs = [...document.querySelectorAll('.ph img[src]')];
    const bad = imgs.filter(i => !i.naturalWidth).length;
    return { total: imgs.length, unloaded: bad };
  });
}

// 几何级变形判据（M2）。两条容易写错的地方，都是实测换来的：
//   ① object-fit 的 CSS 初始值是 fill——只判"盒子比 vs 素材比"会在 contain 的定值画框上误报
//      （画框故意统一比例、内容 contain 留白，此时内容并未变形）。
//      真变形只发生在 fill/none 下：内容被拉伸填满盒子。
//   ② contain/cover 下内容不变形，只存在"留白/裁切偏大"，属观感问题，给提示不计 FAIL。
const DEFORM_CHECK = `[...document.querySelectorAll('img')]
  .filter(i => i.naturalWidth && i.naturalHeight)
  .map(i => {
    const r = i.getBoundingClientRect();
    if (r.width < 20 || r.height < 20) return null;
    const fit = getComputedStyle(i).objectFit;
    const nat = i.naturalWidth / i.naturalHeight;
    const box = r.width / r.height;
    return { k: i.getAttribute('data-img') || i.className || '(img)', fit: fit,
             nat: +nat.toFixed(3), box: +box.toFixed(3), dev: +Math.abs(box / nat - 1).toFixed(3) };
  }).filter(Boolean)`;

async function deformCheck(page, label) {
  // 先分段滚一遍全页，让懒注入的图片全部就位（否则只查得到视口附近的图）
  await page.evaluate(async () => {
    const d = document.scrollingElement;
    const h = d.scrollHeight - window.innerHeight;
    for (const f of [0, 0.25, 0.5, 0.75, 1]) {
      d.scrollTop = h * f;
      await new Promise(r => setTimeout(r, 350));
    }
  });
  await page.waitForTimeout(800);
  const all = await page.evaluate(DEFORM_CHECK);
  const stretched = all.filter(x => (x.fit === 'fill' || x.fit === 'none') && x.dev > 0.02);
  const loose = all.filter(x => (x.fit === 'contain' || x.fit === 'cover') && x.dev > 0.35);
  check(all.length > 0, label + ' 几何级判据有样本', '已核查 ' + all.length + ' 张图');
  check(stretched.length === 0, label + ' 图片无拉伸变形（fill/none 且偏离 >2%）',
    stretched.length ? JSON.stringify(stretched.slice(0, 4)) : '共 ' + all.length + ' 张已核查');
  if (loose.length) {
    log('  提示: ' + loose.length + ' 张图框比例与素材偏离 >35%'
      + '（contain/cover 下内容不变形，仅留白偏大，不计 FAIL）:: '
      + JSON.stringify(loose.slice(0, 3)));
  }
}

// 坑 16：定值画框是双 img 结构（bg 装饰模糊底 + fg 主体），点击放大会误开装饰层。
// 只在主体图已加载时判，未加载则跳过——不给假红也不给假绿。
const ZOOM_CHECK = `(() => {
  const ph = document.querySelector('.vs-img') || document.querySelector('.ph.zoomable');
  if (!ph) return null;
  const fg = ph.querySelector('img.fg') || ph.querySelector('img');
  if (!fg || !fg.src) return null;
  ph.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  const lb = document.getElementById('lb');
  if (!lb) return null;
  const got = { on: lb.classList.contains('on'), same: lb.querySelector('img').src === fg.src };
  lb.classList.remove('on');
  return got;
})()`;

// 导航锚点必须能落到真实章节——缺了不会报错，只表现为点击无反应
const ANCHOR_CHECK = `(() => {
  const links = [...document.querySelectorAll('#nav a[href^="#"]')]
    .map(a => a.getAttribute('href').slice(1));
  return { nav: links.length, bad: links.filter(id => !document.getElementById(id)) };
})()`;

async function goto(page, url) {
  const errors = [];
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('pageerror', e => errors.push('PAGEERROR: ' + e.message));
  await page.goto(url, { waitUntil: 'load' });
  await page.waitForTimeout(1200);
  return errors;
}

// 内容图 cover 判据：装饰模糊底（.bg）允许 cover，内容图必须 0（坑 2）
const COVER_CHECK = `[...document.querySelectorAll('img')]
  .filter(i => !i.classList.contains('bg'))
  .filter(i => getComputedStyle(i).objectFit === 'cover').length`;

async function panCheck(page) {
  await page.evaluate(() => { document.scrollingElement.scrollLeft = 80; });
  return page.evaluate(() => document.scrollingElement.scrollLeft);
}

async function shot(page, name) {
  if (!WANT_SHOTS) return;
  await page.screenshot({ path: path.join(OUTDIR, STAMP + '_' + name + '.png') });
}

(async () => {
  fs.mkdirSync(OUTDIR, { recursive: true });
  log('产物: ' + path.resolve(HTML));
  log('chromium: ' + CHROME);
  log('输出目录: ' + OUTDIR);
  log('');

  const browser = await chromium.launch({ executablePath: CHROME });

  // ================= Ａ 桌面 =================
  {
    log('[桌面 ' + DESKTOP.join('x') + ']');
    const ctx = await browser.newContext({
      viewport: { width: DESKTOP[0], height: DESKTOP[1] },
      reducedMotion: 'no-preference',
    });
    const page = await ctx.newPage();
    const errors = await goto(page, fileUrl);

    const hasTourLib = await page.evaluate(() => !!(window.gsap && window.ScrollTrigger));
    const tour = await page.evaluate(() => document.documentElement.classList.contains('tour'));
    log('  漫游库=' + hasTourLib + ' html.tour=' + tour);
    if (hasTourLib) check(tour === true, '桌面漫游层应启用');

    const refs = await page.evaluate(() => {
      const keys = [...document.querySelectorAll('img[data-img]')].map(i => i.dataset.img);
      const have = window.__IMG__ || {};
      return { refs: new Set(keys).size, keys: Object.keys(have).length,
               missing: [...new Set(keys.filter(k => !(k in have)))],
               unused: Object.keys(have).filter(k => !keys.includes(k)) };
    });
    // 只比数量不够：把一张图的 key 写错后两边都是 5，5===5 仍 PASS，而该图已永远空白。
    // 必须比差集（这是坑 6「key 不统一导致全图 KeyError」的守护）。
    check(refs.missing.length === 0, '图片引用键全部有内联数据',
      refs.missing.length ? '缺失: ' + refs.missing.join(',')
                          : refs.refs + ' 个引用键全部命中');
    check(refs.unused.length === 0, '无未被引用却已内联的图',
      refs.unused.length ? '冗余: ' + refs.unused.join(',')
                         : refs.keys + ' 个内联键全部被引用');

    const cover = await page.evaluate(COVER_CHECK);
    check(cover === 0, '内容图 object-fit:cover 应为 0', '实测 ' + cover);

    // 中段定位（先销毁平滑滚动库再滚，然后等图加载完）
    await page.evaluate(KILL_SMOOTH);
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight * 0.45));
    const loaded = await settle(page);
    check(loaded.unloaded === 0, '中段图片全部加载', loaded.unloaded + ' 张未加载 / 共 ' + loaded.total);
    await shot(page, 'desktop_mid');

    // 横向不可拖动（中段处最严）
    const pan = await panCheck(page);
    check(pan === 0, '桌面横向不可拖动', 'scrollLeft=' + pan);

    if (NOWRAP) {
      const nw = await page.evaluate(sel =>
        [...document.querySelectorAll(sel)].map(e => e.getClientRects().length), NOWRAP);
      check(nw.every(n => n === 1), '不拆行判据 ' + NOWRAP, JSON.stringify(nw));
    }

    // 钉住判据：粘住区间内 pin 元素 top 应为 0（祖先链 overflow 会静默毒杀 sticky，见坑 8）
    if (PIN) {
      const pin = await page.evaluate(sel => {
        const el = document.querySelector(sel);
        if (!el) return null;
        const top = el.getBoundingClientRect().top + window.scrollY;
        window.scrollTo(0, top + 200);
        return { pos: getComputedStyle(el).position };
      }, PIN);
      if (!pin) { check(false, '钉住判据 ' + PIN, '选择器未命中'); }
      else {
        await page.waitForTimeout(1200);
        const top = await page.evaluate(sel => document.querySelector(sel).getBoundingClientRect().top, PIN);
        check(Math.abs(top) < 2, '钉住判据 ' + PIN + '（position:' + pin.pos + '）', 'top=' + top.toFixed(1));
      }
    }

    // 几何级（M2）：分段滚完全页后逐张核拉伸变形——坑 12「压扁 57%」就靠这条抓
    await deformCheck(page, '桌面');

    // 坑 16：定值画框是双 img 结构，点击放大必须取主体图 img.fg，不能取装饰模糊底
    const zoom = await page.evaluate(ZOOM_CHECK);
    if (zoom) {
      check(zoom.on && zoom.same, '点击放大取主体图（img.fg 而非装饰模糊底）', JSON.stringify(zoom));
    }

    // 导航锚点必须落到真实章节——缺了不报错，只表现为"点了没反应"
    const anchors = await page.evaluate(ANCHOR_CHECK);
    if (anchors.nav) {
      check(anchors.bad.length === 0, '导航锚点全部有对应章节',
        anchors.bad.length ? '悬空: ' + anchors.bad.join(',') : anchors.nav + ' 个锚点全部命中');
    }

    // 首屏 / 尾屏
    await page.evaluate(() => window.scrollTo(0, 0));
    await settle(page, 800);
    await shot(page, 'desktop_hero');
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
    await settle(page, 1200);
    await shot(page, 'desktop_outro');

    check(errors.length === 0, '桌面控制台 0 错误', errors.slice(0, 3).join(' | '));
    for (const sel of EXTRA_SHOTS) {
      await page.evaluate(s => {
        const el = document.querySelector(s);
        if (el) window.scrollTo(0, el.getBoundingClientRect().top + window.scrollY - 60);
      }, sel);
      await settle(page, 1200);
      await shot(page, 'desktop_' + sel.replace(/[^a-zA-Z0-9]/g, '_'));
    }
    await ctx.close();
    log('');
  }

  // ================= Ｂ 手机 =================
  {
    log('[手机 ' + MOBILE.join('x') + ']');
    const ctx = await browser.newContext({
      viewport: { width: MOBILE[0], height: MOBILE[1] },
      reducedMotion: 'no-preference',
      isMobile: true, hasTouch: true,
      userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1',
    });
    const page = await ctx.newPage();
    const errors = await goto(page, fileUrl);

    const tour = await page.evaluate(() => document.documentElement.classList.contains('tour'));
    check(tour === false, '手机端漫游层应降级', 'html.tour=' + tour);

    await page.evaluate(KILL_SMOOTH);
    // 三个点位测横向可拖动（只在单点测会漏，见 SKILL.md 第 7 段）
    let panBad = 0;
    for (const frac of [0.15, 0.5, 0.85]) {
      await page.evaluate(f => {
        const d = document.scrollingElement;
        d.scrollTop = (d.scrollHeight - window.innerHeight) * f;
      }, frac);
      await page.waitForTimeout(600);
      const pan = await panCheck(page);
      if (pan !== 0) panBad++;
    }
    check(panBad === 0, '手机端三处点位横向不可拖动', panBad + ' 处可拖动');

    const cover = await page.evaluate(COVER_CHECK);
    check(cover === 0, '手机内容图 object-fit:cover 应为 0', '实测 ' + cover);

    const refs = await page.evaluate(() => {
      const keys = [...document.querySelectorAll('img[data-img]')].map(i => i.dataset.img);
      const have = window.__IMG__ || {};
      return { refs: new Set(keys).size, keys: Object.keys(have).length,
               missing: [...new Set(keys.filter(k => !(k in have)))],
               unused: Object.keys(have).filter(k => !keys.includes(k)) };
    });
    check(refs.missing.length === 0, '手机图片引用键全部有内联数据',
      refs.missing.length ? '缺失: ' + refs.missing.join(',') : refs.refs + ' 个引用键全部命中');
    check(refs.unused.length === 0, '手机无未被引用却已内联的图',
      refs.unused.length ? '冗余: ' + refs.unused.join(',') : refs.keys + ' 个内联键全部被引用');

    await deformCheck(page, '手机');

    if (NOWRAP) {
      const nw = await page.evaluate(sel =>
        [...document.querySelectorAll(sel)].map(e => e.getClientRects().length), NOWRAP);
      check(nw.every(n => n === 1), '手机不拆行 ' + NOWRAP, JSON.stringify(nw));
    }

    await page.evaluate(() => {
      const d = document.scrollingElement;
      d.scrollTop = (d.scrollHeight - window.innerHeight) * 0.3;
    });
    await settle(page, 1200);
    await shot(page, 'mobile_mid');
    await page.evaluate(() => window.scrollTo(0, 0));
    await settle(page, 900);
    await shot(page, 'mobile_hero');

    check(errors.length === 0, '手机控制台 0 错误', errors.slice(0, 3).join(' | '));
    await ctx.close();
    log('');
  }

  // ================= Ｃ 禁用 JS =================
  {
    log('[禁用 JS]');
    const ctx = await browser.newContext({
      viewport: { width: 1280, height: 800 }, javaScriptEnabled: false,
    });
    const page = await ctx.newPage();
    await page.goto(fileUrl, { waitUntil: 'load' });
    await page.waitForTimeout(600);

    const nojs = await page.evaluate(() => {
      const key = [...document.querySelectorAll('.reveal, .ph img, h2, section p')];
      return {
        text: document.body.innerText.length,
        figs: document.querySelectorAll('img[data-img]').length,
        tour: document.documentElement.classList.contains('tour'),
        reveal: (() => { const r = document.querySelector('.reveal');
          return r ? getComputedStyle(r).opacity : 'n/a'; })(),
        content: key.length,
        // innerText 不排除 opacity:0 的元素——只查文本长度会让"全页透明"照样 PASS（坑 5）
        hidden: key.filter(e => {
          const s = getComputedStyle(e);
          return s.display === 'none' || s.visibility === 'hidden' || parseFloat(s.opacity) < 0.05;
        }).length,
      };
    });
    log('  正文长度=' + nojs.text + ' 图片占位=' + nojs.figs + ' tour=' + nojs.tour
      + ' reveal opacity=' + nojs.reveal + ' 不可见关键元素=' + nojs.hidden + '/' + nojs.content);
    check(nojs.text > 200, '禁用 JS 正文仍可读', '长度 ' + nojs.text);
    check(nojs.tour === false, '禁用 JS 不启用漫游层');
    check(nojs.figs > 0, '禁用 JS 图片占位仍在');
    check(nojs.content > 0, '禁用 JS 关键元素存在（判据有样本）', '共 ' + nojs.content);
    check(nojs.hidden === 0, '禁用 JS 关键元素可见（不只看文本存在）',
      nojs.hidden + ' 个不可见 / 共 ' + nojs.content);
    await shot(page, 'nojs');
    await ctx.close();
    log('');
  }

  await browser.close();

  log('---------------------------------------------');
  log(fails === 0 ? 'VERIFY RESULT: PASS（全部判据通过）' : 'VERIFY RESULT: FAIL（' + fails + ' 项未通过）');
  log('截图与报告: ' + OUTDIR);
  fs.writeFileSync(path.join(OUTDIR, STAMP + '_verify_summary.txt'), lines.join('\n'), 'utf-8');
  process.exit(fails === 0 ? 0 : 1);
})().catch(e => {
  log('FATAL: ' + e.message + '\n' + (e.stack || ''));
  try {
    fs.mkdirSync(OUTDIR, { recursive: true });
    fs.writeFileSync(path.join(OUTDIR, STAMP + '_verify_summary.txt'), lines.join('\n'), 'utf-8');
  } catch (e2) {}
  process.exit(1);
});
