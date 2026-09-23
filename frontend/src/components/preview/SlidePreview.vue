<template>
  <div class="slide-preview">
    <!-- 16:9 画布：几何完全对齐 backend/services/generator.py 的 pptx 版面 -->
    <div class="slide-frame">
      <div class="slide-stage" :class="[`place-${placement}`, { 'has-caption': showCaptionBelow }]">
        <img
          v-if="fullBleed && imageUrl"
          class="stage-image is-fullbleed"
          :src="imageUrl"
          alt=""
        />
        <div v-if="fullBleed && imageUrl" class="stage-scrim" aria-hidden="true"></div>

        <button
          type="button"
          class="stage-title"
          :class="{ 'is-active': isActive('title') }"
          @click="select('title')"
        >
          {{ slide.title || '（未命名页面）' }}
        </button>

        <button
          v-if="slide.purpose"
          type="button"
          class="stage-purpose"
          :class="{ 'is-active': isActive('purpose') }"
          @click="select('purpose')"
        >
          {{ slide.purpose }}
        </button>

        <div v-if="imageUrl && !fullBleed" class="stage-image-frame" :class="`is-${placement}`">
          <img class="stage-image" :src="imageUrl" :alt="caption" />
        </div>

        <!-- 版面结构由 layout 决定，四种结构化版式与导出的 pptx 用同一套判据 -->
        <div v-if="!visibleBullets.length" class="stage-body">
          <p class="stage-hint">这一页还没有投影要点</p>
        </div>

        <div v-else-if="layout === 'cards'" class="stage-body stage-cards">
          <button
            v-for="card in cardEntries"
            :key="card.index"
            type="button"
            class="card-button"
            :class="{ 'is-active': isActive('bullets', card.index) }"
            @click="select('bullets', card.index)"
          >
            <span v-if="card.label" class="card-rule" aria-hidden="true"></span>
            <span v-if="card.label" class="card-label">{{ card.label }}</span>
            <span class="card-body">{{ card.body }}</span>
          </button>
        </div>

        <div v-else-if="layout === 'flow'" class="stage-body stage-flow">
          <template v-for="(stage, index) in flowStages" :key="index">
            <button
              type="button"
              class="flow-button"
              :class="{ 'is-active': isActive('bullets', index) }"
              @click="select('bullets', index)"
            >
              {{ stage }}
            </button>
            <span v-if="index < flowStages.length - 1" class="flow-arrow" aria-hidden="true"></span>
          </template>
        </div>

        <div v-else-if="layout === 'metric'" class="stage-body stage-metrics">
          <button
            v-for="metric in metricEntries"
            :key="metric.index"
            type="button"
            class="metric-button"
            :class="{ 'is-active': isActive('bullets', metric.index), 'is-plain': !metric.figure }"
            @click="select('bullets', metric.index)"
          >
            <span v-if="metric.figure" class="metric-figure">{{ metric.figure }}</span>
            <span class="metric-label">{{ metric.label }}</span>
          </button>
        </div>

        <div v-else-if="layout === 'quote'" class="stage-body stage-quote">
          <span class="quote-bar" aria-hidden="true"></span>
          <div class="quote-lines">
            <button
              v-for="line in quoteLines"
              :key="line.index"
              type="button"
              class="quote-line"
              :class="{ 'is-active': isActive('bullets', line.index) }"
              @click="select('bullets', line.index)"
            >
              {{ line.text }}
            </button>
            <button
              v-if="attribution"
              type="button"
              class="quote-attribution"
              :class="{ 'is-active': isActive('bullets', attributionIndex) }"
              @click="select('bullets', attributionIndex)"
            >
              — {{ attribution }}
            </button>
          </div>
        </div>

        <ol v-else class="stage-body stage-bullets">
          <li v-for="(bullet, index) in visibleBullets" :key="index">
            <button
              type="button"
              class="bullet-button"
              :class="{ 'is-active': isActive('bullets', index) }"
              @click="select('bullets', index)"
            >
              <span class="bullet-marker" aria-hidden="true">•</span>
              <span class="bullet-text">{{ bullet }}</span>
            </button>
          </li>
        </ol>

        <p v-if="showCaptionBelow" class="stage-caption">{{ caption }}</p>

        <span class="stage-page">第 {{ slide.order }} 页</span>
      </div>
    </div>

    <!-- 讲稿不会投影到屏幕上，所以单独放在画布下方 -->
    <div class="notes-strip">
      <span class="notes-caption">讲稿</span>
      <button
        type="button"
        class="notes-button"
        :class="{ 'is-active': isActive('speaker_notes') }"
        @click="select('speaker_notes')"
      >
        {{ slide.speaker_notes || '（暂无讲稿，点击可补充）' }}
      </button>
    </div>

    <p v-if="overflowCount" class="overflow-note">
      还有 {{ overflowCount }} 条超出本版式容量，导出时会并入讲稿
    </p>

    <p v-if="placement === 'background' && imageUrl && caption" class="scrim-note">
      背景图说明「{{ caption }}」会并入讲稿，不显示在画面上
    </p>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  /** 单页幻灯片快照（SlideSpec） */
  slide: { type: Object, required: true },
  /** 已解析好的图片地址；空字符串表示这一页没有配图或图片取不到 */
  imageUrl: { type: String, default: '' },
  /** 当前选中的字段名，用于高亮 */
  activeField: { type: String, default: '' },
  /** 当前选中的列表条目下标；null 表示整个字段 */
  activeIndex: { type: Number, default: null },
})

const emit = defineEmits(['select'])

const PLACEMENTS = ['right', 'full', 'background']

/** 与后端一致：非法值退回 right，避免预览和导出出现分歧。 */
const placement = computed(() => {
  const value = String(props.slide.image?.placement || 'right')
  return PLACEMENTS.includes(value) ? value : 'right'
})
const caption = computed(() => String(props.slide.image?.caption || ''))
/**
 * 满版模式（背景图 / 整页大图）铺满整页，图注没有"图下方"的位置，
 * 后端会把它并入讲稿。
 */
const fullBleed = computed(() => ['background', 'full'].includes(placement.value))
const showCaptionBelow = computed(() => Boolean(caption.value) && !fullBleed.value)

// ── 版式 ────────────────────────────────────────────────
// 快照写入时后端已把版式名归一，所以这里只需要认识规范值本身，
// 不必再抄一份同义词表——两份表必然漂移，那正是预览与导出不一致的来源。
const layout = computed(() => (
  String(props.slide.layout || '').trim().toLowerCase().replace(/[-\s]+/g, '_')
))

const bulletsList = computed(() => (props.slide.bullets || []).map(item => String(item)))

// 与后端 generator 的版式容量一致：卡片 4、流程 4、度量 3、步骤 5。超出的条目
// 后端会并入讲稿，预览必须用同样的切分才不会比导出多画出内容。
const LAYOUT_CAPACITY = { cards: 4, flow: 4, metric: 3, steps: 5 }
const visibleBullets = computed(() => (
  bulletsList.value.slice(0, LAYOUT_CAPACITY[layout.value] ?? bulletsList.value.length)
))
const overflowCount = computed(() => bulletsList.value.length - visibleBullets.value.length)

/** 与后端 _split_card_label 一致：只有小标题足够短时才拆成两层。 */
const CARD_LABEL_MAX_CHARS = 8
function splitCardLabel(text) {
  for (const colon of ['：', ':']) {
    const at = text.indexOf(colon)
    if (at <= 0) continue
    const label = text.slice(0, at).trim()
    const body = text.slice(at + 1).trim()
    if (body && label.length <= CARD_LABEL_MAX_CHARS) return { label, body }
  }
  return { label: '', body: text.trim() }
}

const cardEntries = computed(() => (
  visibleBullets.value.map((text, index) => ({ index, ...splitCardLabel(text) }))
))
const flowStages = computed(() => visibleBullets.value)

// 与后端 METRIC_PATTERN 一致：只认"开头就是数字"，提不出就整条当普通短句。
const METRIC_PATTERN = /^\s*([+\-]?\d[\d.,]*\s*(?:%|％|℃|°C|度|倍|万|亿|分钟|小时|天|周|年|个|人|次|条|项|分|秒)?)/
const metricEntries = computed(() => visibleBullets.value.map((text, index) => {
  const match = METRIC_PATTERN.exec(text)
  const figure = match ? match[1].trim() : ''
  const label = match ? text.slice(match[0].length).replace(/^[：:，,、\-—\s]+/, '').trim() : ''
  return figure && label ? { index, figure, label } : { index, figure: '', label: text }
}))

// 与后端 _render_quote 一致：以破折号开头的最后一条是出处，不留在正文里。
const ATTRIBUTION_PREFIXES = ['——', '—', '--']
const quote = computed(() => {
  const entries = visibleBullets.value
  const last = (entries[entries.length - 1] || '').trim()
  if (entries.length > 1 && ATTRIBUTION_PREFIXES.some(prefix => last.startsWith(prefix))) {
    return {
      lines: entries.slice(0, -1),
      attribution: last.replace(/^[—-]+/, '').trim(),
      attributionIndex: entries.length - 1,
    }
  }
  return { lines: entries, attribution: '', attributionIndex: null }
})
const quoteLines = computed(() => quote.value.lines.map((text, index) => ({ index, text })))
const attribution = computed(() => quote.value.attribution)
const attributionIndex = computed(() => quote.value.attributionIndex)

function isActive(field, index = null) {
  return props.activeField === field && props.activeIndex === index
}

/** 把点击位置换算成后端的元素锚点：字段 + 可选下标。 */
function select(field, index = null) {
  emit('select', { field, index })
}
</script>

<style scoped>
.slide-preview {
  display: grid;
  gap: 10px;
  min-width: 0;
}

/* 容器宽度决定画布内所有字号，缩放时版式比例保持不变 */
.slide-frame {
  container-type: inline-size;
  width: 100%;
}

/*
 * 所有定位都用百分比，数值直接来自 pptx 渲染器（13.333in x 7.5in）：
 *   内容区     左 4.87%  上 22.93%  宽 90.25%  高 60.67%
 *   正文（右图）左 6.75%  宽 40.5%   其余同内容区
 *   图片（右图）左 50%    宽 45.12%
 *   图注       上 78.27%  高 5.33%
 *   来源/页码  上 90%
 */
.slide-stage {
  position: relative;
  aspect-ratio: 16 / 9;
  overflow: hidden;
  border: 1px solid #d8dfe9;
  border-radius: 10px;
  background: #fff;
  box-shadow: 0 12px 32px rgba(15, 23, 42, 0.08);
}

.stage-title,
.stage-purpose,
.stage-caption {
  position: absolute;
}

/*
 * 正文区：标准要点页与四种结构化版式共用同一起点，
 * 位置取自 backend/services/generator.py 的 TEXT_BOXES。
 */
.stage-body {
  position: absolute;
  left: 6.75%;
  top: 23.33%;
  width: 87%;
  max-height: 60.67%;
  overflow: hidden;
}

.stage-title {
  left: 4.87%;
  top: 7.33%;
  width: 90%;
  height: 12%;
  font-size: 3.2cqw;
  font-weight: 700;
  line-height: 1.25;
  color: #0f172a;
  overflow: hidden;
}

.stage-purpose {
  left: 6.75%;
  top: 20.2%;
  width: 87%;
  font-size: 1.7cqw;
  font-style: italic;
  color: #64748b;
}

.stage-bullets {
  display: grid;
  gap: 1.4cqw;
  margin: 0;
  padding: 0;
  list-style: none;
}

/* 右侧图文：正文让出右半区 */
.place-right .stage-body {
  width: 40.5%;
}

.stage-hint {
  margin: 0;
  font-size: 1.7cqw;
  color: #94a3b8;
}

/*
 * ── 结构化版式 ──────────────────────────────────────
 * 字号由 pptx 的磅值换算：画布宽 13.333in = 960pt，即 1cqw = 9.6pt，
 * 所以 Npt 对应 N/9.6 cqw；间距按英寸折算（1in = 7.5cqw）。
 */

/* 并列卡片：N 张等宽面板，间距 0.32in */
.stage-cards {
  display: grid;
  grid-auto-flow: column;
  grid-auto-columns: 1fr;
  gap: 2.4cqw;
  align-items: start;
}

.card-button {
  display: grid;
  gap: 0.7cqw;
  padding: 2.2cqw 1.5cqw;
  border: 1px solid #d3e0f5;
  border-radius: 8px;
  background: #f5f8ff;
  font-family: inherit;
  text-align: left;
  cursor: pointer;
}

.card-rule {
  width: 2.5cqw;
  height: 0.42cqw;
  border-radius: 2px;
  background: #1463ff;
}

.card-label {
  font-size: 1.77cqw; /* 17pt */
  font-weight: 700;
  line-height: 1.35;
  color: #0b3fa8;
}

.card-body {
  min-width: 0;
  font-size: 1.56cqw; /* 15pt */
  line-height: 1.45;
  color: #1e293b;
}

/* 流程：横向等宽块 + 块间箭头（后端箭头宽 0.30in，落在 0.62in 的间隙里） */
.stage-flow {
  display: flex;
  align-items: stretch;
}

.flow-button {
  flex: 1 1 0;
  min-width: 0;
  padding: 2.4cqw 1.4cqw;
  border: 1px solid #d3e0f5;
  border-radius: 8px;
  background: #f5f8ff;
  font-family: inherit;
  font-size: 1.56cqw; /* 15pt */
  line-height: 1.45;
  color: #1e293b;
  text-align: left;
  cursor: pointer;
}

.flow-arrow {
  flex: none;
  width: 4.65cqw;
  display: grid;
  place-items: center;
}

.flow-arrow::before {
  content: '';
  width: 0;
  height: 0;
  border-left: 2.25cqw solid #1463ff;
  border-top: 0.83cqw solid transparent;
  border-bottom: 0.83cqw solid transparent;
}

/* 度量：巨号数字 + 说明 */
.stage-metrics {
  display: grid;
  grid-auto-flow: column;
  grid-auto-columns: 1fr;
  gap: 2.7cqw;
  align-items: stretch;
}

.metric-button {
  display: grid;
  align-content: center;
  justify-items: center;
  gap: 1.4cqw;
  padding: 2.4cqw 1.4cqw;
  border: 1px solid #d3e0f5;
  border-radius: 8px;
  background: #f5f8ff;
  font-family: inherit;
  text-align: center;
  cursor: pointer;
}

.metric-figure {
  font-size: 4.58cqw; /* 44pt */
  font-weight: 700;
  line-height: 1.1;
  color: #1463ff;
}

.metric-label {
  font-size: 1.46cqw; /* 14pt */
  line-height: 1.5;
  color: #64748b;
}

/* 提不出数字的条目按普通短句排，与后端的降级一致 */
.metric-button.is-plain {
  align-content: start;
  justify-items: start;
  text-align: left;
}

.metric-button.is-plain .metric-label {
  font-size: 1.67cqw; /* 16pt */
  color: #1e293b;
}

/* 引用：左侧主色竖条标出引文边界，出处右对齐排在下方 */
.stage-quote {
  display: grid;
  grid-template-columns: 0.64cqw minmax(0, 1fr);
  gap: 2.8cqw;
  align-items: start;
}

.quote-bar {
  align-self: stretch;
  min-height: 22cqw;
  background: #1463ff;
}

.quote-lines {
  display: grid;
  gap: 1.2cqw;
}

.quote-line {
  padding: 0.35cqw 0.6cqw;
  border: 1px solid transparent;
  border-radius: 6px;
  background: transparent;
  font-family: inherit;
  font-size: 2.08cqw; /* 20pt */
  line-height: 1.45;
  color: #0b3fa8;
  text-align: left;
  cursor: pointer;
}

.quote-attribution {
  padding: 0.35cqw 0.6cqw;
  border: 1px solid transparent;
  border-radius: 6px;
  background: transparent;
  font-family: inherit;
  font-size: 1.35cqw; /* 13pt */
  color: #64748b;
  text-align: right;
  cursor: pointer;
}

.card-button:hover,
.flow-button:hover,
.metric-button:hover,
.quote-line:hover,
.quote-attribution:hover {
  border-color: #c7dcff;
}

.card-button.is-active,
.flow-button.is-active,
.metric-button.is-active,
.quote-line.is-active,
.quote-attribution.is-active {
  border-color: #1463ff;
  background: #e6f0ff;
}

.stage-caption {
  left: 6.75%;
  top: 78.27%;
  height: 5.33%;
  width: 87%;
  margin: 0;
  font-size: 1.5cqw;
  color: #64748b;
  overflow: hidden;
}

.place-right .stage-caption {
  left: 50%;
  width: 45.12%;
}

.stage-page {
  position: absolute;
  right: 5.12%;
  top: 90%;
  height: 4.67%;
  font-size: 1.4cqw;
  color: #94a3b8;
}

/* 图片框：contain 居中，和后端的等比缩放一致 */
.stage-image-frame {
  position: absolute;
  top: 22.93%;
  height: 60.67%;
  display: grid;
  place-items: center;
  overflow: hidden;
}

.stage-image-frame.is-right { left: 50%; width: 45.12%; }

/* 有图注时图片让出底部一条，和后端收缩图片框的做法一致 */
.has-caption .stage-image-frame { height: 55.34%; }

.stage-image {
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
}

/* 满版图（背景图 / 整页大图）铺满整页，用 cover 裁切（后端是等价的比例裁剪） */
.stage-image.is-fullbleed {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  max-width: none;
  max-height: none;
  object-fit: cover;
}

/* 后端在满版图上叠的是"白→透明"渐变蒙层（透明度见 generator.SCRIM_*），
   纯色会让图片发灰，所以这里也必须用渐变，预览与导出才一致 */
.stage-scrim {
  position: absolute;
  inset: 0;
  background: linear-gradient(
    180deg,
    rgba(255, 255, 255, 0.92) 0%,
    rgba(255, 255, 255, 0.8) 62%,
    rgba(255, 255, 255, 0.28) 100%
  );
}

/* 画布内每一处可点选内容共用同一套交互外观 */
.stage-title,
.stage-purpose,
.bullet-button,
.notes-button {
  display: block;
  box-sizing: border-box;
  margin: 0;
  padding: 0.35cqw 0.8cqw;
  border: 1px solid transparent;
  border-radius: 6px;
  background: transparent;
  font-family: inherit;
  text-align: left;
  cursor: pointer;
  transition: background-color 0.15s ease, border-color 0.15s ease;
}

.bullet-button {
  display: flex;
  align-items: flex-start;
  gap: 0.8cqw;
  width: 100%;
  font-size: 2cqw;
  line-height: 1.35;
  color: #1e293b;
}

.bullet-marker {
  flex: none;
  color: #1463ff;
  font-weight: 700;
}

.bullet-text { min-width: 0; }

.stage-title:hover,
.stage-purpose:hover,
.bullet-button:hover,
.notes-button:hover {
  background: #eef4ff;
  border-color: #c7dcff;
}

.stage-title.is-active,
.stage-purpose.is-active,
.bullet-button.is-active,
.notes-button.is-active {
  background: #e6f0ff;
  border-color: #1463ff;
}

.notes-strip {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 10px;
  align-items: start;
  padding: 10px 12px;
  border: 1px dashed #cbd5e1;
  border-radius: 8px;
  background: #f8fafc;
}

.notes-caption {
  padding-top: 3px;
  color: #64748b;
  font-size: 12px;
  font-weight: 600;
}

.notes-button {
  width: 100%;
  padding: 3px 8px;
  color: #475569;
  font-size: 13px;
  line-height: 1.55;
}

.scrim-note {
  margin: 0;
  color: #94a3b8;
  font-size: 12px;
}

.overflow-note {
  margin: 0;
  color: #c2410c;
  font-size: 12px;
}
</style>
