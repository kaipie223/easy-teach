<template>
  <div ref="host" class="aurora" aria-hidden="true">
    <canvas ref="canvas" class="aurora-canvas" />
    <div class="aurora-scrim" />
  </div>
</template>

<script setup>
/**
 * Stripe 首页 hero 渐变的移植版。
 *
 * 实现取自 stripe.com 首页的生产代码（chunk pages/index-*.js）：
 *   - 全屏平面 + 正交相机（这里等价成原生 WebGL 的全屏三角形）
 *   - 片元着色器与 Stripe 逐行一致：simplex noise → 2 octave fbm →
 *     f*4 分四段 mix 四色 → 等高线阴影（丝绸波纹质感的来源）
 *   - 材质参数照抄：u_noise_scale=0.5、u_contour_lines=10、
 *     u_time*0.00002、u_seed=Math.random()、隔帧渲染（≈30fps）
 *
 * 与 Stripe 的差异只有两点：配色换成站点的"蓝→紫→粉→橙"彩虹系
 * （Stripe 当前官网用的是红粉系，四色结构相同）；不加 Three.js 依赖。
 */
import { onBeforeUnmount, onMounted, ref } from 'vue'

const host = ref(null)
const canvas = ref(null)

// 顶点着色器：全屏两三角形，v_uv 0..1（与 Stripe 平面几何的 uv 等价）
const VERTEX_SHADER = `
attribute vec2 a_position;
varying vec2 v_uv;
void main() {
  v_uv = a_position * 0.5 + 0.5;
  gl_Position = vec4(a_position, 0.0, 1.0);
}
`

// 片元着色器：与 stripe.com hero 的波浪着色器逐行一致
const FRAGMENT_SHADER = `
precision highp float;

uniform float u_time;
uniform float u_seed;
uniform float u_contour_lines;
uniform float u_noise_scale;

uniform vec3 u_color1;
uniform vec3 u_color2;
uniform vec3 u_color3;
uniform vec3 u_color4;

// 鼠标交互（本站新增，Stripe 原版没有）
uniform vec2 u_pointer;
uniform float u_pointer_strength;
uniform float u_aspect;

varying vec2 v_uv;

const vec4 C = vec4(0.211324865405187, 0.366025403784439, -0.577350269189626, 0.024390243902439);
const mat2 rot = mat2(0.87758256, 0.47942554, -0.47942554, 0.87758256);
const vec2 shift = vec2(100.0);

vec3 permute(vec3 x) {
  return mod(((x * 34.0) + 1.0) * x, 289.0);
}

float snoise(vec2 v) {
  vec2 i  = floor(v + dot(v, C.yy));
  vec2 x0 = v - i + dot(i, C.xx);

  vec2 i1 = (x0.x > x0.y) ? vec2(1.0, 0.0) : vec2(0.0, 1.0);

  vec4 x12 = x0.xyxy + C.xxzz;
  x12.xy -= i1;

  i = mod(i, 289.0);
  vec3 p = permute(permute(i.y + vec3(0.0, i1.y, 1.0)) + i.x + vec3(0.0, i1.x, 1.0));

  vec3 m = max(0.5 - vec3(dot(x0, x0), dot(x12.xy, x12.xy), dot(x12.zw, x12.zw)), 0.0);
  m = m * m;
  m = m * m;

  vec3 x = 2.0 * fract(p * C.www) - 1.0;
  vec3 h = abs(x) - 0.5;
  vec3 ox = floor(x + 0.5);
  vec3 a0 = x - ox;

  m *= 1.79284291400159 - 0.85373472095314 * (a0 * a0 + h * h);

  vec3 g;
  g.x = a0.x * x0.x + h.x * x0.y;
  g.yz = a0.yz * x12.xz + h.yz * x12.yw;

  return 130.0 * dot(m, g);
}

float fbm(vec2 x, float time) {
  float v = 0.0;
  v += 0.5 * snoise(x + time);
  x = rot * x * 2.0 + shift;
  v += 0.25 * snoise(x + time);
  return v;
}

void main() {
  // 指针附近对采样坐标做柔和的"透镜"位移：鼠标推着色带走，并轻微提亮。
  // 乘 u_aspect 保证影响范围是正圆而不是椭圆。
  vec2 toPointer = (v_uv - u_pointer) * vec2(u_aspect, 1.0);
  float pointerDistance = length(toPointer);
  float pointerFalloff = exp(-pointerDistance * 6.0) * u_pointer_strength;

  vec2 pos = v_uv * u_noise_scale;
  pos += (toPointer / (pointerDistance + 0.001)) * pointerFalloff * 0.14;

  float timeOffset = u_time * 0.00002 + u_seed;

  float f = fbm(pos, timeOffset);
  f = (f + 1.0) * 0.5;

  float colorStep = f * 4.0;
  float blend1 = clamp(colorStep, 0.0, 1.0);
  float blend2 = clamp(colorStep - 1.0, 0.0, 1.0);
  float blend3 = clamp(colorStep - 2.0, 0.0, 1.0);

  vec3 color = u_color1;
  color = mix(color, u_color2, blend1);
  color = mix(color, u_color3, blend2);
  color = mix(color, u_color4, blend3);

  float contour = fract(f * u_contour_lines);

  float shadow = smoothstep(0.6, 1.0, contour);
  float shadowStrength = (1.0 - step(0.5, blend3)) * 0.1;

  color = mix(color, color * (1.0 - shadowStrength), shadow);

  // 指针处轻微提亮，让"跟手"能被看见
  color += 0.06 * pointerFalloff;

  gl_FragColor = vec4(color, 1.0);
}
`

/* 站点彩虹配色（对齐参考图的蓝→紫→粉→橙；Stripe 原值为红粉系，四色结构相同） */
const COLORS = [
  [0.263, 0.325, 0.851], // 靛蓝
  [0.478, 0.361, 0.941], // 紫
  [0.941, 0.376, 0.62], // 玫粉
  [1.0, 0.631, 0.408], // 橙
]

let gl = null
let uniforms = {}
let frameId = 0
let frameTick = 0

/* 指针跟随：目标值由 pointermove 更新，实际值每帧向目标缓动（避免生硬跳变） */
const pointer = { x: 0.5, y: 0.5, targetX: 0.5, targetY: 0.5, strength: 0, targetStrength: 0 }
const EASING = 0.08

function trackPointer(event) {
  const rect = host.value?.getBoundingClientRect()
  if (!rect || !rect.width || !rect.height) return
  const x = (event.clientX - rect.left) / rect.width
  // 屏幕 y 向下、着色器 uv 向上，需要翻转
  const y = 1 - (event.clientY - rect.top) / rect.height
  pointer.targetX = Math.min(1, Math.max(0, x))
  pointer.targetY = Math.min(1, Math.max(0, y))
  pointer.targetStrength = 1
}

function releasePointer() {
  pointer.targetStrength = 0
}

function advancePointer() {
  pointer.x += (pointer.targetX - pointer.x) * EASING
  pointer.y += (pointer.targetY - pointer.y) * EASING
  pointer.strength += (pointer.targetStrength - pointer.strength) * EASING
}

function uploadPointer() {
  gl.uniform2f(uniforms.pointer, pointer.x, pointer.y)
  gl.uniform1f(uniforms.pointerStrength, pointer.strength)
}

function compileShader(type, source) {
  const shader = gl.createShader(type)
  gl.shaderSource(shader, source)
  gl.compileShader(shader)
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    console.warn('[aurora] 着色器编译失败：', gl.getShaderInfoLog(shader))
    return null
  }
  return shader
}

function setupWebGL() {
  gl = canvas.value.getContext('webgl', { antialias: false, alpha: false, depth: false })
  if (!gl) return false

  const vertexShader = compileShader(gl.VERTEX_SHADER, VERTEX_SHADER)
  const fragmentShader = compileShader(gl.FRAGMENT_SHADER, FRAGMENT_SHADER)
  if (!vertexShader || !fragmentShader) return false

  const program = gl.createProgram()
  gl.attachShader(program, vertexShader)
  gl.attachShader(program, fragmentShader)
  gl.linkProgram(program)
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
    console.warn('[aurora] 程序链接失败：', gl.getProgramInfoLog(program))
    return false
  }
  gl.useProgram(program)

  // 全屏两三角形
  const buffer = gl.createBuffer()
  gl.bindBuffer(gl.ARRAY_BUFFER, buffer)
  gl.bufferData(
    gl.ARRAY_BUFFER,
    new Float32Array([-1, -1, 3, -1, -1, 3]),
    gl.STATIC_DRAW,
  )
  const positionLocation = gl.getAttribLocation(program, 'a_position')
  gl.enableVertexAttribArray(positionLocation)
  gl.vertexAttribPointer(positionLocation, 2, gl.FLOAT, false, 0, 0)

  uniforms = {
    time: gl.getUniformLocation(program, 'u_time'),
    seed: gl.getUniformLocation(program, 'u_seed'),
    contour: gl.getUniformLocation(program, 'u_contour_lines'),
    noiseScale: gl.getUniformLocation(program, 'u_noise_scale'),
    pointer: gl.getUniformLocation(program, 'u_pointer'),
    pointerStrength: gl.getUniformLocation(program, 'u_pointer_strength'),
    aspect: gl.getUniformLocation(program, 'u_aspect'),
    colors: [1, 2, 3, 4].map((index) => gl.getUniformLocation(program, `u_color${index}`)),
  }

  // contour_lines 照抄（10）。noise_scale 上调：Stripe 官网四色同属红粉一族，
  // 0.5 的大尺度就能保证整幅同族渐变；我们是蓝→紫→粉→橙的跨色相彩虹，
  // 尺度太小时整个画面只落在一两个色带里（实测整片粉）。调大后四个色带都能进画面。
  gl.uniform1f(uniforms.contour, 10)
  gl.uniform1f(uniforms.noiseScale, 1.2)
  gl.uniform1f(uniforms.seed, Math.random())
  COLORS.forEach((color, index) => {
    gl.uniform3f(uniforms.colors[index], color[0], color[1], color[2])
  })
  return true
}

function resize() {
  const dpr = Math.min(window.devicePixelRatio || 1, 1.5)
  const width = Math.max(1, Math.round(canvas.value.clientWidth * dpr))
  const height = Math.max(1, Math.round(canvas.value.clientHeight * dpr))
  if (canvas.value.width !== width || canvas.value.height !== height) {
    canvas.value.width = width
    canvas.value.height = height
    gl?.viewport(0, 0, width, height)
    gl?.uniform1f(uniforms.aspect, canvas.value.width / canvas.value.height)
  }
}

function render(time) {
  advancePointer()
  uploadPointer()
  gl.uniform1f(uniforms.time, time)
  gl.drawArrays(gl.TRIANGLES, 0, 3)
}

onMounted(() => {
  if (!setupWebGL()) {
    // WebGL 不可用：退一层静态 CSS 渐变，页面依旧成立
    host.value.style.background =
      'linear-gradient(120deg, var(--gradient-brand-soft, transparent), transparent 70%)'
    canvas.value.style.display = 'none'
    return
  }
  resize()
  gl.uniform1f(uniforms.aspect, canvas.value.width / canvas.value.height)
  render(0)

  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches

  // 鼠标/触控交互：指针移动时重新着色。降低动效偏好下不跑常驻动画循环，
  // 但仍然响应指针（一次手势一帧，不做持续动画）。
  let pending = false
  const redrawOnPointer = () => {
    if (pending) return
    pending = true
    requestAnimationFrame(() => {
      pending = false
      resize()
      render(performance.now())
    })
  }
  window.addEventListener('pointermove', trackPointer, { passive: true })
  window.addEventListener('pointerdown', trackPointer, { passive: true })
  window.addEventListener('pointerleave', releasePointer)
  window.addEventListener('blur', releasePointer)

  if (reducedMotion) {
    window.addEventListener('pointermove', redrawOnPointer, { passive: true })
    return
  }
  const observer = new ResizeObserver(() => {
    resize()
    render(performance.now())
  })
  observer.observe(host.value)

  // Stripe 用 frameInterval=2（隔帧渲染 ≈30fps）：环境动效足够顺滑又省电
  const loop = (time) => {
    frameTick += 1
    if (frameTick % 2 === 0) {
      resize()
      render(time)
    }
    frameId = requestAnimationFrame(loop)
  }
  frameId = requestAnimationFrame(loop)
})

onBeforeUnmount(() => {
  if (frameId) cancelAnimationFrame(frameId)
  window.removeEventListener('pointermove', trackPointer)
  window.removeEventListener('pointerdown', trackPointer)
  window.removeEventListener('pointerleave', releasePointer)
  window.removeEventListener('blur', releasePointer)
  gl?.getExtension('WEBGL_lose_context')?.loseContext()
  gl = null
})
</script>

<style scoped>
.aurora {
  position: absolute;
  inset: 0;
  overflow: hidden;
  pointer-events: none;
}

.aurora-canvas {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  display: block;
}

/* 白纱：铺满全屏时不能让大片高饱和色压住正文。
   注意 background 简写里纯色只能作为最后一层，所以"整体白罩"用渐变表达；
   层序：①整体降饱和白罩（在最上）②左上更白（标题/表单一侧）③底部收白 */
.aurora-scrim {
  position: absolute;
  inset: 0;
  background:
    linear-gradient(
      color-mix(in srgb, var(--bg-surface) 58%, transparent),
      color-mix(in srgb, var(--bg-surface) 58%, transparent)
    ),
    linear-gradient(
      115deg,
      color-mix(in srgb, var(--bg-surface) 80%, transparent) 0%,
      color-mix(in srgb, var(--bg-surface) 30%, transparent) 45%,
      color-mix(in srgb, var(--bg-surface) 0%, transparent) 75%
    ),
    linear-gradient(
      to bottom,
      color-mix(in srgb, var(--bg-surface) 0%, transparent) 55%,
      var(--bg-surface) 100%
    );
}
</style>
