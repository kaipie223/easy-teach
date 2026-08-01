export const dashboardStats = [
  { key: 'activeTasks', label: '进行中任务', value: 6, icon: 'Clock' },
  { key: 'completedLessons', label: '已完成课件', value: 24, icon: 'CircleCheck' },
  { key: 'pendingMaterials', label: '待处理资料', value: 13, icon: 'Document' },
  { key: 'recentExports', label: '最近导出', value: 8, icon: 'Upload' },
]

export const teachingTasks = [
  {
    id: 'task-001',
    name: '初中物理-浮力',
    subject: '浮力及其应用',
    audience: '初二学生',
    status: '进行中',
    updatedAt: '2025-05-12 14:30',
  },
  {
    id: 'task-002',
    name: '高中语文-赤壁赋',
    subject: '赤壁赋文本解读',
    audience: '高二学生',
    status: '已完成',
    updatedAt: '2025-05-11 09:15',
  },
  {
    id: 'task-003',
    name: '英语写作-议论文',
    subject: '议论文结构与论证',
    audience: '高一学生',
    status: '进行中',
    updatedAt: '2025-05-10 16:45',
  },
  {
    id: 'task-004',
    name: '历史-辛亥革命',
    subject: '辛亥革命的背景与意义',
    audience: '高一学生',
    status: '草稿',
    updatedAt: '2025-05-09 11:20',
  },
  {
    id: 'task-005',
    name: '数学-二次函数',
    subject: '二次函数图像与性质',
    audience: '初三学生',
    status: '已完成',
    updatedAt: '2025-05-08 10:05',
  },
]

export const recentExports = [
  {
    id: 'export-001',
    name: '浮力及其应用.pptx',
    type: 'PPTX',
    exportedAt: '2025-05-12 14:25',
    size: '18.6 MB',
  },
  {
    id: 'export-002',
    name: '赤壁赋教案.docx',
    type: 'DOCX',
    exportedAt: '2025-05-11 09:10',
    size: '32.4 KB',
  },
  {
    id: 'export-003',
    name: '互动课堂-浮力.html',
    type: 'HTML',
    exportedAt: '2025-05-10 16:40',
    size: '9.8 MB',
  },
]

export const chatMessages = [
  {
    id: 'msg-001',
    role: 'teacher',
    sender: '张老师',
    time: '10:15',
    content: '我想设计一节初二物理课，主题是浮力及其应用，希望包含生活中的实例和实验探究。',
  },
  {
    id: 'msg-002',
    role: 'assistant',
    sender: 'TeachMate AI',
    time: '10:15',
    content: '好的，我将根据您的想法生成初步教学需求。请确认以下关键信息是否准确，或补充更多细节。',
  },
  {
    id: 'msg-003',
    role: 'teacher',
    sender: '张老师',
    time: '10:16',
    content: '授课对象是初二学生，课时安排为 1 课时。',
  },
  {
    id: 'msg-004',
    role: 'assistant',
    sender: 'TeachMate AI',
    time: '10:16',
    content: '明白了，我已更新需求信息。还需要明确教学目标或重点难点吗？',
  },
]

export const requirementForm = {
  topic: '浮力及其应用',
  audience: '初二学生',
  duration: '1课时',
  objectives: '理解浮力的概念，掌握影响浮力大小的因素，能运用浮力知识解释生活现象。',
  coreKnowledge: '浮力的概念、阿基米德原理、浮力大小的影响因素',
  focusAndDifficulties: '重点：影响浮力大小的因素；难点：阿基米德原理的理解与应用',
  outputType: 'PPT课件 + 教案 + 互动练习',
}

export const outputTypeOptions = [
  'PPT课件',
  'PPT课件 + 教案',
  'PPT课件 + 教案 + 互动练习',
  'HTML互动包',
]

export const materials = [
  {
    id: 'material-001',
    name: '数据库教材.pdf',
    type: 'PDF',
    size: '8.6 MB',
    progress: 100,
    status: '已解析',
    usage: '内容依据',
    scope: '全局',
  },
  {
    id: 'material-002',
    name: '旧课件.pptx',
    type: 'PPTX',
    size: '12.4 MB',
    progress: 100,
    status: '已解析',
    usage: '知识结构参考',
    scope: '全局',
  },
  {
    id: 'material-003',
    name: '课堂讲解.mp4',
    type: 'MP4',
    size: '95.2 MB',
    progress: 60,
    status: '解析中',
    usage: '案例来源',
    scope: '部分章节',
  },
  {
    id: 'material-004',
    name: '流程图.png',
    type: 'PNG',
    size: '1.3 MB',
    progress: 0,
    status: '失败',
    usage: '互动素材',
    scope: '互动环节',
  },
  {
    id: 'material-005',
    name: '教案.docx',
    type: 'DOCX',
    size: '2.7 MB',
    progress: 100,
    status: '已解析',
    usage: '内容依据',
    scope: '全局',
  },
]

export const evidenceSnips = [
  {
    id: 'evidence-001',
    source: 'PDF第12页',
    type: 'PDF',
    content: '数据库事务的 ACID 特性定义与示例说明。',
  },
  {
    id: 'evidence-002',
    source: 'PPT第6页',
    type: 'PPT',
    content: '关系模型的结构与键的概念图示。',
  },
  {
    id: 'evidence-003',
    source: '视频01:23',
    type: 'Video',
    content: '讲解索引优化的原理与实际应用案例。',
  },
  {
    id: 'evidence-004',
    source: '图片OCR',
    type: 'Image',
    content: '流程图：数据库设计流程步骤与分支说明。',
  },
]

export const blueprintSummary = {
  topic: '数据库基础与应用',
  audience: '大二计算机专业本科生',
  duration: '8课时',
  scope: '全课程',
}

export const blueprintPlan = [
  {
    id: 'stage-001',
    stage: '导入激趣',
    duration: '0.5课时',
    knowledge: '数据库的定义与价值',
    activity: '情境导入、问题引导',
    evidence: 'PDF第12页',
    pages: '1-2',
  },
  {
    id: 'stage-002',
    stage: '概念讲解',
    duration: '2课时',
    knowledge: '数据模型、关系模型、键',
    activity: '讲授、示例演示',
    evidence: 'PDF第12页',
    pages: '3-8',
  },
  {
    id: 'stage-003',
    stage: '案例探究',
    duration: '2课时',
    knowledge: 'ER建模与规范化',
    activity: '案例分析、小组讨论',
    evidence: '视频01:23',
    pages: '9-14',
  },
  {
    id: 'stage-004',
    stage: '互动练习',
    duration: '2课时',
    knowledge: 'SQL查询与索引优化',
    activity: '上机练习、随堂测验',
    evidence: '旧课件第6页',
    pages: '15-20',
  },
  {
    id: 'stage-005',
    stage: '总结拓展',
    duration: '1.5课时',
    knowledge: '知识梳理与前沿拓展',
    activity: '总结回顾、拓展阅读',
    evidence: '知识库',
    pages: '21-24',
  },
]

export const generationChecks = [
  { key: 'objective', label: '目标完整', passed: true },
  { key: 'materials', label: '资料已绑定', passed: true },
  { key: 'order', label: '知识点顺序清晰', passed: true },
  { key: 'interaction', label: '互动环节已包含', passed: true },
]

export const slidePages = Array.from({ length: 12 }, (_, index) => ({
  id: `slide-${String(index + 1).padStart(2, '0')}`,
  page: index + 1,
  title: index === 1 ? '数据库索引原理' : `页面 ${index + 1}`,
  selected: index === 1,
}))

export const currentSlide = {
  page: 2,
  total: 12,
  title: '数据库索引原理',
  bullets: ['索引的定义与作用', '索引的数据结构', 'B+ 树索引详解', '索引维护与代价', '索引选择最佳实践'],
  mediaType: 'video',
  zoom: 100,
}

export const editorSuggestions = [
  '标题层级可更清晰',
  '增强数据结构图示',
  '补充性能对比示例',
  '增加索引选择场景',
]

export const sourceReferences = ['PDF第12页', '视频01:23', '知识库']

export const exportVersion = {
  version: 'v1.2.0',
  author: '张老师',
  updatedAt: '2025-05-26 14:30',
  summary: '12页PPT，1份教案，4个互动',
  status: '已完成',
}

export const exportQualityChecks = [
  { key: 'objective', label: '目标一致', passed: true },
  { key: 'source', label: '来源完整', passed: true },
  { key: 'overflow', label: '页面无溢出', passed: true },
  { key: 'interactive', label: '互动可运行', passed: true },
]

export const exportFormats = [
  { key: 'pptx', label: 'PPTX课件', status: '已生成', icon: 'Document' },
  { key: 'docx', label: 'DOCX教案', status: '已生成', icon: 'Tickets' },
  { key: 'html', label: 'HTML互动包', status: '已生成', icon: 'Connection' },
]

export const versionHistory = [
  {
    version: 'v1.2.0',
    current: true,
    updatedAt: '2025-05-26 14:30',
    note: '优化索引结构图，补充性能对比示例',
  },
  {
    version: 'v1.1.0',
    current: false,
    updatedAt: '2025-05-24 10:15',
    note: '完善索引维护与代价部分内容',
  },
  {
    version: 'v1.0.0',
    current: false,
    updatedAt: '2025-05-22 09:40',
    note: '初始版本创建',
  },
]

export const teachMateMock = {
  dashboardStats,
  teachingTasks,
  recentExports,
  chatMessages,
  requirementForm,
  outputTypeOptions,
  materials,
  evidenceSnips,
  blueprintSummary,
  blueprintPlan,
  generationChecks,
  slidePages,
  currentSlide,
  editorSuggestions,
  sourceReferences,
  exportVersion,
  exportQualityChecks,
  exportFormats,
  versionHistory,
}
