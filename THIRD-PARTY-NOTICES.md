# 第三方组件许可声明

本仓库**不内置**下列第三方库的源码；它们仅在构建产物时按需从 npm 获取并内联到产物中。
以下是对许可条款的整理，便于使用者判断许可边界，**不构成法律意见**。

---

## GSAP 3.15 — `gsap.min.js` / `ScrollTrigger.min.js`

- **许可**：Standard "No Charge" License — <https://gsap.com/standard-license>
- **允许**：在任何网站与应用中实现和使用，含商业用途。
- **禁止**：
  - 用于"让用户无需写代码即可构建可视化动画"、并与 Webflow 竞争的工具；
  - 反向工程以制作竞品；
  - 移除品牌声明。
- **重要**：该许可**不允许单独分发 GSAP 本体**，因此本仓库**不附带** `gsap.min.js`。
  请在构建时自行从 npm 获取并内联到产物中。

## Lenis 1.3.26 — `lenis.min.js`

- **许可**：MIT License
- **版权**：Copyright (c) 2024 darkroom.engineering

---

用于商业产品前，请自行复核 GSAP 现行许可文本。
