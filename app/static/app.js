const state = {
  config: null,
  token: localStorage.getItem('flowcodex_token') || '',
  projects: [],
  dashboard: null,
  activeProject: null,
  projectRuns: [],
  requirements: [],
  activeRun: null,
  runTab: 'overview',
  modalOpen: false,
};

const STATUS_LABEL = {
  queued: '排队中', running: '运行中', awaiting_input: '待补充信息', awaiting_plan_approval: '待确认方案',
  blocked: '已拦截', completed: '已完成', failed: '失败', cancelled: '已取消', open: '待开始', in_progress: '进行中',
};
const STAGE_LABEL = {
  intake: '接收', clarify: '需求访谈', feasibility: '可行性', plan: '技术方案', code: '编码',
  review: '代码审查', verify: '验证', deliver: '交付', distill: '知识蒸馏',
};
const SCORE_LABEL = {
  clarity: '需求清晰度', repo_match: '仓库匹配度', scope_control: '范围可控性',
  technical_simplicity: '技术简单度', dependency_readiness: '依赖就绪度',
  risk_control: '风险可控性', delivery_shape: '交付形态',
};

const PROVIDER_LABEL = {
  mock: '模拟模式',
  deepseek: 'DeepSeek',
  openai: 'OpenAI',
  'openai-compatible': '兼容接口',
};
const MODEL_LABEL = { mock: '模拟模型' };
const REVIEW_VERDICT_LABEL = { pass: '通过', changes_requested: '需要修改' };
const SEVERITY_LABEL = { critical: '关键问题', suggestion: '改进建议' };
const SECURITY_CHECK_LABEL = {
  auth_from_session: '用户身份取自登录态',
  auth_middleware: '接口已接入鉴权',
  parameterized_sql: 'SQL 参数化',
  no_hardcoded_secrets: '无硬编码敏感信息',
  input_validation: '外部入参校验',
  resource_ownership: '资源归属校验',
};
const KNOWLEDGE_KIND_LABEL = {
  lesson: '经验',
  pitfall: '踩坑',
  plan_template: '方案模板',
  prd_template: '需求模板',
  architecture: '架构知识',
};
const KNOWLEDGE_STATUS_LABEL = { accepted: '已收录', pending_review: '待复核' };

function providerLabel(value) {
  return PROVIDER_LABEL[String(value || '').toLowerCase()] || String(value || '—');
}
function modelLabel(value) {
  return MODEL_LABEL[String(value || '').toLowerCase()] || String(value || '—');
}
function reviewVerdictLabel(value) {
  return REVIEW_VERDICT_LABEL[value] || '未审查';
}
function severityLabel(value) {
  return SEVERITY_LABEL[value] || value || '—';
}
function securityLabel(value) {
  return SECURITY_CHECK_LABEL[value] || value;
}
function knowledgeKindLabel(value) {
  return KNOWLEDGE_KIND_LABEL[value] || value || '知识';
}
function knowledgeStatusChip(value) {
  const statusClass = value === 'accepted' ? 'completed' : 'awaiting_plan_approval';
  return `<span class="status-chip status-${statusClass}">${esc(KNOWLEDGE_STATUS_LABEL[value] || value || '—')}</span>`;
}

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
const shortId = (value) => String(value || '').slice(0, 10);
const fmtDate = (value) => {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
};
const statusChip = (status) => `<span class="status-chip status-${esc(status)}">${esc(STATUS_LABEL[status] || status)}</span>`;
const stageChip = (stage) => `<span class="stage-chip">${esc(STAGE_LABEL[stage] || stage)}</span>`;

async function api(path, options = {}) {
  const headers = { 'Content-Type': 'application/json', ...(options.headers || {}) };
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  const response = await fetch(`/api${path}`, { ...options, headers });
  if (response.status === 401) {
    showTokenModal(true);
    throw new Error('需要访问令牌');
  }
  const contentType = response.headers.get('content-type') || '';
  const data = contentType.includes('application/json') ? await response.json() : await response.text();
  if (!response.ok) {
    const rawDetail = data && typeof data === 'object' ? data.detail : data;
    const detail = Array.isArray(rawDetail)
      ? rawDetail.map((item) => {
          let message = item?.msg || JSON.stringify(item);
          message = String(message).replace(/^Value error,\s*/, '');
          return message === 'Field required' ? '必填项不能为空' : message;
        }).join('；')
      : rawDetail;
    throw new Error(detail || `请求失败（HTTP ${response.status}）`);
  }
  return data;
}

function setBreadcrumb(text) { const el = $('#breadcrumb'); if (el) el.textContent = text; }
function toast(message, type = 'info') {
  const root = $('#toast-root');
  if (!root) return;
  root.innerHTML = '';
  const node = document.createElement('div');
  node.className = `toast ${type}`;
  node.textContent = message;
  root.appendChild(node);
  setTimeout(() => node.remove(), 4200);
}
function showModal(html) {
  $('#modal-root').innerHTML = `<div class="modal-backdrop"><div class="modal">${html}</div></div>`;
  state.modalOpen = true;
  setTimeout(() => $('.modal input, .modal textarea')?.focus(), 30);
}
function closeModal() { $('#modal-root').innerHTML = ''; state.modalOpen = false; }
function loadingView(label = '正在加载…') {
  $('#view').innerHTML = `<div class="empty-state"><div class="spinner"></div><p>${esc(label)}</p></div>`;
}
function errorView(error) {
  $('#view').innerHTML = `<div class="card card-pad"><div class="notice danger"><div><strong>请求失败</strong><div>${esc(error.message || error)}</div></div></div></div>`;
}

function renderSidebar(active = 'dashboard') {
  const projectList = $('#project-list');
  if (projectList) {
    projectList.innerHTML = state.projects.length
      ? state.projects.map((project) => `
          <a class="project-link ${state.activeProject?.id === project.id ? 'active' : ''}" href="#/project/${project.id}">
            <span class="project-avatar">${esc((project.name || 'P').slice(0, 1).toUpperCase())}</span>
            <span>${esc(project.name)}</span>
          </a>`).join('')
      : `<div class="muted tiny" style="padding:6px 10px">还没有项目</div>`;
  }
  $$('.nav-item').forEach((item) => item.classList.toggle('active', item.dataset.nav === active));
  const provider = $('#provider-badge');
  if (provider && state.config) {
    provider.textContent = state.config.mock ? '模拟模式 · 本地演示' : `模型服务：${providerLabel(state.config.llm_provider)} · 模型：${modelLabel(state.config.llm_model)}`;
    provider.classList.toggle('mock', Boolean(state.config.mock));
  }
  const env = $('#app-env');
  if (env && state.config) env.textContent = `${state.config.app_name} · 版本 v${state.config.version}`;
}

function onboardingCard() {
  return `<div class="onboarding"><div class="orb">⌁</div><h2>先接入一个代码仓库</h2><p>FlowCodex 只会操作 仓库根目录下的 Git 仓库。每个运行都会创建独立的隔离工作区，不会直接污染主工作区。</p><button class="btn btn-primary" data-action="new-project">创建第一个项目</button></div>`;
}
function renderProjectCard(project) {
  return `<a class="card card-subtle card-pad" href="#/project/${project.id}" style="display:block"><div style="display:flex;align-items:center;justify-content:space-between;gap:12px"><div style="display:flex;align-items:center;gap:10px;min-width:0"><span class="project-avatar">${esc((project.name || 'P').slice(0, 1).toUpperCase())}</span><strong style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(project.name)}</strong></div><span class="tag">${project.auto_apply ? '自动提交' : '隔离待提交'}</span></div><div class="muted small mono" style="margin-top:10px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(project.repo_path)}</div></a>`;
}
function renderRunRow(run) {
  const title = run.requirement_title || run.plan?.summary || `需求 ${shortId(run.requirement_id)}`;
  return `<a class="run-row" href="#/run/${run.id}"><div style="min-width:0"><div class="title">${esc(title)}</div><div class="meta"><span>${esc(shortId(run.id))}</span><span>·</span><span>${esc(fmtDate(run.updated_at || run.created_at))}</span><span>·</span><span>${esc(STAGE_LABEL[run.stage] || run.stage)}</span></div></div><div class="run-row-right">${statusChip(run.status)}<span class="muted">→</span></div></a>`;
}
function requirementForm(projects, selectedId) {
  return `<form class="form" data-form="requirement"><div class="field"><label for="req-project">项目</label><select id="req-project" name="project_id">${projects.map((project) => `<option value="${project.id}" ${project.id === selectedId ? 'selected' : ''}>${esc(project.name)}</option>`).join('')}</select></div><div class="field"><label for="req-title">需求标题</label><input id="req-title" name="title" placeholder="例如：给帖子列表增加缓存命中率指标" required maxlength="160"></div><div class="field"><label for="req-input">需求描述</label><textarea id="req-input" name="raw_input" placeholder="描述用户遇到的问题、期望的结果；不需要一开始就写成技术方案。" required></textarea><span class="hint">越具体，第一轮访谈越短。但模糊需求也可以提交，智能体会先追问。</span></div><div class="form-actions"><button class="btn btn-primary" type="submit">提交并启动流水线 →</button></div></form>`;
}

function renderDashboard() {
  setBreadcrumb('工作台');
  renderSidebar('dashboard');
  const counts = state.dashboard?.counts || {};
  const knowledge = state.dashboard?.knowledge || {};
  const recent = state.dashboard?.recent_runs || [];
  const hasProjects = state.projects.length > 0;
  $('#view').innerHTML = `
    <div class="page-head"><div><h1>把一条需求，推到可验证交付。</h1><p class="subtitle">需求访谈 → 可行性门禁 → 技术方案 → 隔离编码 → 代码审查 → 验证 → 交付 → 知识蒸馏。人只在真正需要判断的节点介入。</p></div><div class="head-actions"><button class="btn btn-primary" data-action="new-project">+ 新建项目</button></div></div>
    <div class="grid grid-4" style="margin-bottom:22px">
      <div class="card metric accent"><div class="metric-label">进行中</div><div class="metric-value">${counts.active || 0}</div><div class="metric-foot">排队 / 运行 / 等待决策</div></div>
      <div class="card metric"><div class="metric-label">待人工决策</div><div class="metric-value">${counts.waiting || 0}</div><div class="metric-foot">需求补充或方案确认</div></div>
      <div class="card metric green"><div class="metric-label">已完成</div><div class="metric-value">${counts.completed || 0}</div><div class="metric-foot">含验证结果与交付产物</div></div>
      <div class="card metric blue"><div class="metric-label">知识资产</div><div class="metric-value">${knowledge.accepted || 0}</div><div class="metric-foot">${knowledge.pending_review || 0} 条待复核</div></div>
    </div>
    <div class="grid grid-main"><div class="grid">
      <section class="card"><div class="card-head"><h2>最近运行</h2><a class="text-button" href="#/knowledge">查看知识库 →</a></div><div class="card-body">${recent.length ? `<div class="run-list">${recent.map(renderRunRow).join('')}</div>` : `<div class="notice">还没有运行记录。先创建一个项目，再提交第一条需求。</div>`}</div></section>
      <section class="card"><div class="card-head"><h2>项目</h2><span class="muted small">${state.projects.length} 个</span></div><div class="card-body">${hasProjects ? `<div class="grid grid-2">${state.projects.map(renderProjectCard).join('')}</div>` : onboardingCard()}</div></section>
    </div><aside class="grid">
      <section class="card"><div class="card-head"><h2>提交新需求</h2><span class="tiny muted">一句话也可以</span></div><div class="card-body">${hasProjects ? requirementForm(state.projects, state.projects[0].id) : `<div class="notice warn">请先创建项目并挂载一个 Git 仓库。</div>`}</div></section>
      <section class="card card-subtle"><div class="card-body"><h3 style="margin-bottom:8px">这条流水线的硬规则</h3><div class="list-stack"><div class="list-item"><div class="item-title">先澄清，再执行</div><div class="item-body">目标用户、业务规则、验收标准不清楚时，流程停住，不带着错误理解往下跑。</div></div><div class="list-item"><div class="item-title">方案按依赖排序</div><div class="item-body">垂直切片、步骤可验证、单步控制在 5 个文件以内。</div></div><div class="list-item"><div class="item-title">隔离工作区</div><div class="item-body">补丁先应用到独立分支，审查和验证通过后才允许提交。</div></div></div></div></section>
    </aside></div>`;
}

async function loadDashboard() {
  loadingView('正在加载工作台…');
  try {
    const [dashboard, projects] = await Promise.all([api('/dashboard'), api('/projects')]);
    state.dashboard = dashboard;
    state.projects = projects;
    state.activeProject = null;
    renderDashboard();
  } catch (error) {
    if (error.message !== '需要访问令牌') errorView(error);
  }
}

async function loadProject(projectId) {
  loadingView('正在加载项目…');
  try {
    const [project, requirements, runs] = await Promise.all([
      api(`/projects/${projectId}`), api(`/projects/${projectId}/requirements`), api(`/runs?project_id=${encodeURIComponent(projectId)}`),
    ]);
    state.activeProject = project;
    state.requirements = requirements;
    state.projectRuns = runs;
    if (!state.projects.find((item) => item.id === project.id)) state.projects.unshift(project);
    renderProject();
  } catch (error) {
    if (error.message !== '需要访问令牌') errorView(error);
  }
}

function renderProject() {
  const project = state.activeProject;
  if (!project) return;
  setBreadcrumb(project.name);
  renderSidebar('dashboard');
  const runsByRequirement = new Map(state.projectRuns.map((run) => [run.requirement_id, run]));
  $('#view').innerHTML = `
    <a class="back-link" href="#/">← 返回工作台</a>
    <div class="page-head"><div><h1>${esc(project.name)}</h1><p class="subtitle mono">${esc(project.repo_path)}</p></div><div class="head-actions"><a class="btn" href="#/knowledge">知识库</a><button class="btn btn-primary" data-action="new-project">+ 新项目</button></div></div>
    <div class="grid grid-3" style="margin-bottom:22px">
      <div class="card metric"><div class="metric-label">交付策略</div><div class="metric-value" style="font-size:20px">${project.auto_apply ? '自动提交' : '隔离待提交'}</div><div class="metric-foot">${project.auto_apply ? '验证通过后提交隔离分支' : '保留已暂存补丁，等待人工提交'}</div></div>
      <div class="card metric"><div class="metric-label">验证命令</div><div class="metric-value" style="font-size:16px;font-family:var(--mono)">${esc(project.verify_command || '未配置')}</div><div class="metric-foot">在隔离工作区内执行</div></div>
      <div class="card metric"><div class="metric-label">需求数</div><div class="metric-value">${state.requirements.length}</div><div class="metric-foot">${state.projectRuns.length} 次运行</div></div>
    </div>
    <div class="grid grid-main"><section class="card"><div class="card-head"><h2>需求</h2><span class="muted small">${state.requirements.length} 条</span></div><div class="card-body">${state.requirements.length ? `<table class="table"><thead><tr><th>需求</th><th>状态</th><th>运行</th><th>更新时间</th></tr></thead><tbody>${state.requirements.map((requirement) => { const run = runsByRequirement.get(requirement.id); return `<tr><td class="title-cell">${esc(requirement.title)}<small>${esc(requirement.raw_input.slice(0, 90))}${requirement.raw_input.length > 90 ? '…' : ''}</small></td><td>${statusChip(requirement.status)}</td><td>${run ? `<a class="btn btn-sm btn-green" href="#/run/${run.id}">${statusChip(run.status)}</a>` : `<span class="muted tiny">未启动</span>`}</td><td class="muted small">${esc(fmtDate(requirement.updated_at))}</td></tr>`; }).join('')}</tbody></table>` : `<div class="notice">还没有需求。提交一个描述即可启动第一条流水线。</div>`}</div></section>
    <aside class="grid"><section class="card"><div class="card-head"><h2>提交需求</h2></div><div class="card-body">${requirementForm([project], project.id)}</div></section><section class="card card-subtle"><div class="card-body"><h3 style="margin-bottom:8px">项目配置</h3><dl class="kv"><dt>仓库</dt><dd class="mono">${esc(project.repo_path)}</dd><dt>默认分支</dt><dd>${esc(project.default_branch)}</dd><dt>允许路径</dt><dd>${esc((project.allowed_paths || []).join(', '))}</dd><dt>发布命令</dt><dd class="mono">${esc(project.publish_command || '未配置')}</dd></dl></div></section></aside></div>`;
}
async function loadKnowledge() {
  setBreadcrumb('知识库');
  renderSidebar('knowledge');
  loadingView('正在加载知识库…');
  try {
    const items = await api('/knowledge?include_expired=true');
    $('#view').innerHTML = `
      <div class="page-head"><div><h1>知识库</h1><p class="subtitle">每次交付后自动蒸馏可复用结论。高置信度直接入库，低置信度进入待复核；过期知识会自动降级。</p></div><div class="head-actions"><a class="btn" href="#/">返回工作台</a></div></div>
      ${items.length ? `<div class="grid grid-2">${items.map(renderKnowledgeCard).join('')}</div>` : `<div class="card card-pad"><div class="notice">还没有知识资产。完成一次运行后，智能体会尝试蒸馏一条跨需求可复用的经验。</div></div>`}
    `;
  } catch (error) {
    if (error.message !== '需要访问令牌') errorView(error);
  }
}

function renderKnowledgeCard(item) {
  return `<article class="card card-pad"><div class="section-title"><div><div class="muted tiny" style="margin-bottom:4px">${esc(knowledgeKindLabel(item.kind))}</div><h3>${esc(item.title)}</h3></div>${knowledgeStatusChip(item.status)}</div><p class="muted small" style="white-space:pre-wrap">${esc(item.content)}</p><div class="chips">${(item.tags || []).map((tag) => `<span class="chip">${esc(tag)}</span>`).join('')}</div><div class="card-foot" style="padding:14px 0 0;margin-top:14px;display:flex;align-items:center;justify-content:space-between;gap:10px"><span class="muted tiny">置信度 ${Number(item.confidence || 0).toFixed(2)} · 到期 ${esc((item.expires_at || '').slice(0, 10))}</span><button class="btn btn-sm" data-action="feedback" data-id="${item.id}">反馈失真</button></div></article>`;
}

async function loadRun(runId, silent = false) {
  if (!silent) loadingView('正在加载运行…');
  try {
    const data = await api(`/runs/${runId}`);
    if (!state.projects.length) {
      try { state.projects = await api('/projects'); } catch {}
    }
    const previousRunId = state.activeRun?.run?.id;
    state.activeRun = data;
    state.activeProject = data.project;
    if (previousRunId !== runId || !state.runTab) state.runTab = 'overview';
    renderRunView();
  } catch (error) {
    if (error.message !== '需要访问令牌') errorView(error);
  }
}

function renderRunView() {
  const data = state.activeRun;
  if (!data) return;
  const run = data.run;
  const requirement = data.requirement;
  setBreadcrumb(requirement.title);
  renderSidebar('dashboard');
  $('#view').innerHTML = `
    <a class="back-link" href="#/project/${run.project_id}">← 返回项目</a>
    <div class="page-head"><div style="min-width:0"><h1 style="overflow-wrap:anywhere">${esc(requirement.title)}</h1><p class="subtitle">${esc(requirement.raw_input)}</p></div><div class="head-actions"><span class="tag mono">运行编号 ${esc(shortId(run.id))}</span>${statusChip(run.status)}${stageChip(run.stage)}</div></div>
    ${renderStageRail(data)}
    ${renderRunActionBanner(data)}
    ${renderTabs(data)}
    <div id="run-tab-content">${renderRunTab(data)}</div>`;
}

function renderStageRail(data) {
  const stages = data.stages || [];
  const current = stages.findIndex((stage) => stage.id === data.run.stage);
  const completed = data.run.status === 'completed';
  return `<div class="stage-rail">${stages.map((stage, index) => {
    let cls = '';
    if (completed || index < current) cls = 'done';
    else if (index === current && data.run.status === 'blocked') cls = 'blocked';
    else if (index === current) cls = 'active';
    return `<div class="stage-item ${cls}"><span class="stage-dot"></span><span>${esc(stage.label)}</span></div>`;
  }).join('')}</div>`;
}

function renderRunActionBanner(data) {
  const run = data.run;
  if (run.status === 'awaiting_input') {
    return `<div class="notice warn" style="margin-bottom:16px"><div><strong>流程已暂停：需要补充需求信息。</strong><div>智能体会先复述理解再追问，不带着模糊信息进入开发。</div><div style="margin-top:10px"><button class="btn btn-sm" data-action="set-tab" data-tab="clarify">前往访谈 →</button></div></div></div>`;
  }
  if (run.status === 'awaiting_plan_approval') {
    return `<div class="notice warn" style="margin-bottom:16px"><div style="flex:1"><strong>技术方案已生成，等待你确认。</strong><div>方案按依赖排序、按垂直切片拆分；确认后才会进入隔离编码。</div></div><div style="display:flex;gap:8px;flex-wrap:wrap"><button class="btn btn-green" data-action="approve-plan" data-id="${run.id}">批准方案</button><button class="btn btn-danger" data-action="reject-plan" data-id="${run.id}">驳回</button></div></div>`;
  }
  if (run.status === 'blocked' && run.stage === 'feasibility') {
    return `<div class="notice danger" style="margin-bottom:16px"><div style="flex:1"><strong>可行性门禁未通过。</strong><div>${esc(run.error || '当前需求可能需要人工拆分或补充上下文。')}</div></div><button class="btn btn-sm" data-action="override-feasibility" data-id="${run.id}">人工放行</button></div>`;
  }
  if (run.status === 'blocked' && run.stage === 'plan') {
    return `<div class="notice danger" style="margin-bottom:16px"><div style="flex:1"><strong>方案被人工拦截。</strong><div>${esc(run.error || '方案需要调整。')}</div></div><button class="btn btn-sm" data-action="rework" data-id="${run.id}">重新生成方案</button></div>`;
  }
  if (run.status === 'blocked' && ['review', 'verify', 'code'].includes(run.stage)) {
    return `<div class="notice danger" style="margin-bottom:16px"><div style="flex:1"><strong>${esc(run.error || '当前阶段被拦截')}</strong><div>补齐信息或修复关键问题后，可以带着返工说明重新进入编码。</div></div><button class="btn btn-sm" data-action="rework" data-id="${run.id}">要求返工</button></div>`;
  }
  if (run.status === 'failed') {
    return `<div class="notice danger" style="margin-bottom:16px"><strong>运行失败：</strong> ${esc(run.error || '查看事件日志')}</div>`;
  }
  if (run.status === 'completed') {
    const deliver = run.deliver || {};
    return `<div class="notice success" style="margin-bottom:16px"><div style="flex:1"><strong>交付完成。</strong><div>补丁、审查、验证与知识蒸馏均已结束。${deliver.commit ? ` 已提交：<span class="mono">${esc(deliver.commit.slice(0, 10))}</span>` : ' 分支保留在隔离工作区，可人工提交。'}</div></div><div style="display:flex;gap:8px;flex-wrap:wrap"><button class="btn btn-sm" data-action="download-patch" data-id="${run.id}">下载补丁</button>${deliver.commit ? '' : `<button class="btn btn-sm btn-green" data-action="promote" data-id="${run.id}">提交隔离分支</button>`}<button class="btn btn-sm" data-action="export-obsidian" data-id="${run.id}">导出到 Obsidian</button></div></div>`;
  }
  if (['queued', 'running'].includes(run.status)) {
    return `<div class="notice" style="margin-bottom:16px"><div class="spinner" style="width:18px;height:18px;flex:0 0 auto"></div><div style="flex:1"><strong>${run.status === 'queued' ? '等待后台执行器接管…' : `正在执行：${esc(STAGE_LABEL[run.stage] || run.stage)}`}</strong><div>运行状态不会自动刷新。完成当前阶段后，点击“刷新状态”查看结果。</div></div><button class="btn btn-sm" data-action="refresh">刷新状态</button><button class="btn btn-sm" data-action="cancel-run" data-id="${run.id}">取消</button></div>`;
  }
  if (run.status === 'blocked') {
    return `<div class="notice danger" style="margin-bottom:16px"><strong>运行已拦截：</strong> ${esc(run.error || '请查看事件日志')}</div>`;
  }
  return '';
}

function renderTabs(data) {
  const tabs = [['overview', '概览'], ['clarify', '访谈'], ['feasibility', '可行性'], ['plan', '方案'], ['code', '代码'], ['review', '审查'], ['verify', '验证'], ['knowledge', '知识'], ['events', '事件']];
  return `<div class="tabs">${tabs.map(([id, label]) => `<button class="tab ${state.runTab === id ? 'active' : ''}" data-action="set-tab" data-tab="${id}">${label}${tabBadge(data, id)}</button>`).join('')}</div>`;
}
function tabBadge(data, tab) {
  const run = data.run;
  if (tab === 'clarify' && run.status === 'awaiting_input') return ' ·';
  if (tab === 'plan' && run.status === 'awaiting_plan_approval') return ' ·';
  if (tab === 'review' && (run.review?.issues || []).some((issue) => issue.severity === 'critical')) return ' !';
  return '';
}
function renderRunTab(data) {
  switch (state.runTab) {
    case 'clarify': return renderClarifyTab(data);
    case 'feasibility': return renderFeasibilityTab(data);
    case 'plan': return renderPlanTab(data);
    case 'code': return renderCodeTab(data);
    case 'review': return renderReviewTab(data);
    case 'verify': return renderVerifyTab(data);
    case 'knowledge': return renderKnowledgeTab(data);
    case 'events': return renderEventsTab(data);
    default: return renderOverviewTab(data);
  }
}
function specBlock(spec) {
  if (!spec || !Object.keys(spec).length) return `<div class="notice">尚未生成结构化规格。</div>`;
  return `<dl class="kv"><dt>目标</dt><dd>${esc(spec.goal || '—')}</dd><dt>目标用户</dt><dd>${esc(spec.target_users || '—')}</dd><dt>范围</dt><dd>${(spec.scope || []).map(esc).join('<br>') || '—'}</dd><dt>不做</dt><dd>${(spec.out_of_scope || []).map(esc).join('<br>') || '—'}</dd><dt>业务规则</dt><dd>${(spec.business_rules || []).map(esc).join('<br>') || '—'}</dd><dt>验收标准</dt><dd>${(spec.acceptance_criteria || []).map(esc).join('<br>') || '—'}</dd></dl>`;
}

function renderOverviewTab(data) {
  const run = data.run;
  const spec = data.requirement.spec || run.clarify?.spec || {};
  const feasibility = run.feasibility || {};
  const review = run.review || {};
  const verify = run.verify || {};
  return `<div class="grid grid-main"><div class="grid">
    <section class="card"><div class="card-head"><h2>结构化规格</h2><span class="tag">单一事实源</span></div><div class="card-body">${specBlock(spec)}</div></section>
    <section class="card"><div class="card-head"><h2>运行摘要</h2></div><div class="card-body"><div class="grid grid-2">
      <div class="list-item"><div class="item-title">可行性</div><div class="item-body">${feasibility.total ?? '—'}/100 ${feasibility.total >= (state.config?.feasibility_threshold || 60) ? '· 已通过' : ''}</div></div>
      <div class="list-item"><div class="item-title">审查</div><div class="item-body">${esc(reviewVerdictLabel(review.verdict))}</div></div>
      <div class="list-item"><div class="item-title">验证</div><div class="item-body">${verify.ok ? '通过' : (verify.skipped ? '已跳过' : '未通过/未执行')}</div></div>
      <div class="list-item"><div class="item-title">分支</div><div class="item-body mono">${esc(run.branch_name || '未创建')}</div></div>
    </div></div></section>
  </div><aside class="grid">
    <section class="card card-subtle"><div class="card-body"><h3 style="margin-bottom:10px">最近事件</h3>${renderTimeline(data.events.slice(-5))}</div></section>
    <section class="card card-subtle"><div class="card-body"><h3 style="margin-bottom:8px">运行信息</h3><dl class="kv"><dt>模型服务</dt><dd>${esc(providerLabel(run.llm_provider))}</dd><dt>模型</dt><dd>${esc(modelLabel(run.llm_model))}</dd><dt>令牌用量</dt><dd>输入 ${run.tokens_in || 0} / 输出 ${run.tokens_out || 0}</dd><dt>创建</dt><dd>${esc(fmtDate(run.created_at))}</dd></dl></div></section>
  </aside></div>`;
}

function renderClarifyTab(data) {
  const run = data.run;
  const clarify = run.clarify || {};
  const questions = clarify.questions || [];
  const answers = run.answers || {};
  const form = run.status === 'awaiting_input' && questions.length ? `<form class="form" data-form="clarify" data-id="${run.id}">${questions.map((question) => `<div class="field"><label for="q-${esc(question.id)}">${esc(question.question)} <span class="muted tiny">${question.required ? '必要' : '建议'}</span></label><textarea id="q-${esc(question.id)}" name="q:${esc(question.id)}" placeholder="${esc(question.why || '请补充')}"></textarea></div>`).join('')}<div class="form-actions"><button class="btn btn-primary" type="submit">提交回答，继续流水线 →</button></div></form>` : '';
  return `<div class="grid grid-2">
    <section class="card"><div class="card-head"><h2>理解确认</h2></div><div class="card-body"><p>${esc(clarify.understanding || '尚未完成访谈。')}</p>${(clarify.assumptions || []).length ? `<div style="margin-top:18px"><h3 style="margin-bottom:8px">假设</h3>${(clarify.assumptions || []).map((item) => `<div class="list-item" style="margin-bottom:8px">${esc(item)}</div>`).join('')}</div>` : ''}</div></section>
    <section class="card"><div class="card-head"><h2>访谈问题</h2><span class="tag">${questions.length} 个</span></div><div class="card-body">${form || (questions.length ? `<div class="list-stack">${questions.map((question) => `<div class="list-item"><div class="item-title">${esc(question.question)}</div><div class="item-body">${esc(question.why)}</div><div class="muted tiny" style="margin-top:6px">回答：${esc(answers[question.id] || '—')}</div></div>`).join('')}</div>` : `<div class="notice success">信息已足够，访谈已通过。</div>`)}</div></section>
  </div>`;
}

function renderFeasibilityTab(data) {
  const feasibility = data.run.feasibility || {};
  const rows = Object.entries(SCORE_LABEL).map(([key, label]) => {
    const value = Number(feasibility[key] || 0);
    return `<div class="score-row"><span class="muted">${label}</span><div class="score-bar"><div class="score-fill" style="width:${Math.max(0, Math.min(100, value))}%"></div></div><strong>${value}</strong></div>`;
  }).join('');
  return `<div class="grid grid-main"><section class="card"><div class="card-head"><h2>七维可行性评分</h2><strong style="font-size:26px;color:var(--accent)">${feasibility.total ?? '—'} <span class="muted tiny">/100</span></strong></div><div class="card-body"><div class="grid" style="gap:12px">${rows}</div></div></section>
    <aside class="card"><div class="card-head"><h2>判断依据</h2></div><div class="card-body"><div class="list-stack">${Object.entries(feasibility.reasons || {}).map(([key, value]) => `<div class="list-item"><div class="item-title">${esc(SCORE_LABEL[key] || key)}</div><div class="item-body">${esc(value)}</div></div>`).join('') || '<div class="muted small">暂无</div>'}</div>${(feasibility.blockers || []).length ? `<div class="notice danger" style="margin-top:16px"><strong>阻塞项</strong><div>${(feasibility.blockers || []).map(esc).join('<br>')}</div></div>` : ''}${data.run.status === 'blocked' && data.run.stage === 'feasibility' ? `<button class="btn btn-primary" style="margin-top:16px;width:100%" data-action="override-feasibility" data-id="${data.run.id}">人工放行并进入方案</button>` : ''}</div></aside></div>`;
}

function renderPlanTab(data) {
  const plan = data.run.plan || {};
  const steps = plan.steps || [];
  return `<div class="grid grid-main"><div class="grid">
    <section class="card"><div class="card-head"><h2>技术方案</h2>${data.run.plan_approved ? '<span class="tag">已批准</span>' : ''}</div><div class="card-body">${plan.summary ? `<p style="margin-bottom:18px">${esc(plan.summary)}</p>` : '<div class="notice">尚未生成方案。</div>'}<div class="list-stack">${steps.map((step, index) => `<div class="list-item"><div class="item-head"><div class="item-title">${index + 1}. ${esc(step.title)}</div><span class="tag mono">${esc(step.id)}</span></div><div class="item-body">${(step.changes || []).map(esc).join('<br>') || '—'}</div><div class="chips">${(step.files || []).map((file) => `<span class="chip mono">${esc(file)}</span>`).join('')}</div><div class="muted tiny" style="margin-top:8px">依赖：${esc((step.depends_on || []).join(', ') || '无')} · 验收：${esc((step.acceptance || []).join('; ') || '—')} · 验证：${esc(step.verification || '—')}</div></div>`).join('') || '<div class="muted small">暂无步骤</div>'}</div></div></section>
    ${(plan.checkpoints || []).length ? `<section class="card"><div class="card-head"><h2>验证检查点</h2></div><div class="card-body">${(plan.checkpoints || []).map((item) => `<div class="list-item" style="margin-bottom:8px">${esc(item)}</div>`).join('')}</div></section>` : ''}
  </div><aside class="grid">
    <section class="card"><div class="card-head"><h2>方案自审</h2></div><div class="card-body"><div class="list-stack">${(plan.self_challenge || []).map((item) => `<div class="list-item"><div class="item-title">${esc(item.title)}</div><div class="item-body">${esc(item.detail)}</div><span class="tag" style="margin-top:8px">${esc(severityLabel(item.severity))}</span></div>`).join('') || '<div class="muted small">暂无自审记录</div>'}</div></div></section>
    <section class="card"><div class="card-head"><h2>风险与待确认</h2></div><div class="card-body"><h3 style="margin-bottom:8px">风险</h3>${(plan.risks || []).map((item) => `<div class="list-item" style="margin-bottom:8px">${esc(item)}</div>`).join('') || '<div class="muted small">无</div>'}<h3 style="margin:16px 0 8px">待确认问题</h3>${(plan.unresolved_questions || []).map((item) => `<div class="notice warn" style="margin-bottom:8px">${esc(item)}</div>`).join('') || '<div class="muted small">无</div>'}</div></section>
    ${data.run.status === 'awaiting_plan_approval' ? `<section class="card card-pad"><button class="btn btn-primary" style="width:100%" data-action="approve-plan" data-id="${data.run.id}">批准方案并开始编码</button></section>` : ''}
  </aside></div>`;
}
function renderCodeTab(data) {
  const code = data.run.code || {};
  return `<div class="grid grid-main"><section class="card"><div class="card-head"><h2>补丁</h2>${code.apply_ok ? '<span class="tag">已应用到隔离工作区</span>' : ''}</div><div class="card-body">${code.summary ? `<p style="margin-bottom:12px">${esc(code.summary)}</p>` : ''}<pre class="code-block diff-block">${esc(code.diff || data.run.patch_text || '尚无补丁')}</pre></div></section>
    <aside class="grid"><section class="card"><div class="card-head"><h2>变更摘要</h2></div><div class="card-body"><dl class="kv"><dt>触碰文件</dt><dd>${(code.files_touched || []).map(esc).join('<br>') || '—'}</dd><dt>发现但不碰</dt><dd>${(code.discovered_not_touched || []).map(esc).join('<br>') || '无'}</dd><dt>测试说明</dt><dd>${(code.test_notes || []).map(esc).join('<br>') || '—'}</dd><dt>隔离工作区</dt><dd class="mono">${esc(data.run.worktree_path || '—')}</dd></dl></div></section>
    <section class="card card-subtle"><div class="card-body"><h3 style="margin-bottom:8px">范围纪律</h3><p class="muted small">智能体只能改方案列出的文件。范围外的问题记录在“发现但不碰”，不自行清理。</p></div></section></aside></div>`;
}
function renderReviewTab(data) {
  const review = data.run.review || {};
  const issues = review.issues || [];
  return `<div class="grid grid-main"><section class="card"><div class="card-head"><h2>审查结论</h2>${review.verdict ? `<span class="status-chip ${review.verdict === 'pass' ? 'status-completed' : 'status-blocked'}">${esc(reviewVerdictLabel(review.verdict))}</span>` : ''}</div><div class="card-body">${review.summary ? `<p>${esc(review.summary)}</p>` : '<div class="notice">尚未审查</div>'}<div class="list-stack" style="margin-top:16px">${issues.map((issue) => `<div class="list-item"><div class="item-head"><div class="item-title">${esc(issue.title)}</div><span class="tag" style="color:${issue.severity === 'critical' ? 'var(--danger)' : 'var(--muted)'}">${esc(severityLabel(issue.severity))}</span></div><div class="item-body">${esc(issue.detail)}</div>${issue.file ? `<div class="muted tiny mono" style="margin-top:6px">${esc(issue.file)}</div>` : ''}</div>`).join('') || '<div class="muted small">没有发现问题</div>'}</div></div></section>
    <aside class="grid"><section class="card"><div class="card-head"><h2>安全硬检查</h2></div><div class="card-body"><div class="list-stack">${Object.entries(review.security_checks || {}).map(([key, value]) => `<div class="list-item" style="display:flex;justify-content:space-between;gap:10px"><span class="tiny">${esc(securityLabel(key))}</span><span style="color:${value ? 'var(--green)' : 'var(--danger)'}">${value ? '通过' : '未通过'}</span></div>`).join('') || '<div class="muted small">尚未检查</div>'}</div></div></section>
    <section class="card card-subtle"><div class="card-body"><h3 style="margin-bottom:8px">残余风险</h3>${(review.residual_risks || []).map((item) => `<div class="notice" style="margin-bottom:8px">${esc(item)}</div>`).join('') || '<div class="muted small">无</div>'}</div></section></aside></div>`;
}
function renderVerifyTab(data) {
  const verify = data.run.verify || {};
  return `<div class="grid grid-main"><section class="card"><div class="card-head"><h2>验证结果</h2>${verify.skipped ? '<span class="status-chip status-awaiting_plan_approval">已跳过</span>' : verify.ok ? '<span class="status-chip status-completed">通过</span>' : '<span class="status-chip status-blocked">未通过/未执行</span>'}</div><div class="card-body"><div class="field"><label>命令</label><pre class="code-block">${esc(verify.command || '未配置验证命令')}</pre></div><div class="field" style="margin-top:14px"><label>标准输出</label><pre class="code-block">${esc(verify.stdout || '—')}</pre></div><div class="field" style="margin-top:14px"><label>标准错误</label><pre class="code-block">${esc(verify.stderr || '—')}</pre></div></div></section>
    <aside class="card card-subtle"><div class="card-body"><h3 style="margin-bottom:8px">验证哲学</h3><p class="muted small">编译通过不等于逻辑正确。关键路径要先有会失败的测试，再实现到通过；验证命令在隔离工作区内执行。</p></div></aside></div>`;
}
function renderKnowledgeTab(data) {
  const items = data.knowledge || [];
  return `<div class="grid grid-2">${items.map((item) => `<article class="card card-pad"><div class="section-title"><h3>${esc(item.title)}</h3>${knowledgeStatusChip(item.status)}</div><p class="muted small" style="white-space:pre-wrap">${esc(item.content)}</p><div class="chips">${(item.tags || []).map((tag) => `<span class="chip">${esc(tag)}</span>`).join('')}</div><div class="muted tiny" style="margin-top:10px">置信度 ${Number(item.confidence || 0).toFixed(2)} · 有效期 ${item.ttl_days} 天</div></article>`).join('') || `<div class="card card-pad"><div class="notice">本次运行还没有蒸馏出知识。运行完成后会自动尝试沉淀。</div></div>`}</div>`;
}
function renderEventsTab(data) {
  return `<section class="card"><div class="card-head"><h2>事件时间线</h2><span class="muted small">${(data.events || []).length} 条</span></div><div class="card-body">${renderTimeline(data.events || [])}</div></section>`;
}
function renderTimeline(events) {
  if (!events || !events.length) return '<div class="muted small">暂无事件</div>';
  return `<div class="timeline">${events.map((event) => `<div class="timeline-item ${esc(event.type || '')}"><div class="event-meta">${esc(fmtDate(event.created_at))} · ${esc(STAGE_LABEL[event.stage] || event.stage)}</div><div class="event-msg">${esc(event.message)}</div></div>`).join('')}</div>`;
}

function showProjectModal() {
  showModal(`<div class="modal-head"><div><h2>接入一个 Git 仓库</h2></div><button class="modal-close" data-action="close-modal">×</button></div><div class="modal-body"><form class="form" data-form="project">
    <div class="field"><label for="project-name">项目名称</label><input id="project-name" name="name" placeholder="例如：内容平台" required></div>
    <div class="field"><label for="project-repo">仓库路径</label><input id="project-repo" name="repo_path" placeholder="my-app 或 /repos/my-app" required><span class="hint">相对路径会解析到仓库根目录；必须是一个已有提交的 Git 仓库。</span></div>
    <div class="grid grid-2"><div class="field"><label for="project-branch">默认分支</label><input id="project-branch" name="default_branch" value="main"></div><div class="field"><label for="project-allowed">允许路径（逗号分隔）</label><input id="project-allowed" name="allowed_paths" value="."></div></div>
    <div class="field"><label for="project-verify">验证命令</label><input id="project-verify" name="verify_command" placeholder="例如：pytest -q"><span class="hint">在隔离工作区内执行；留空则跳过验证。</span></div>
    <div class="field"><label for="project-publish">发布命令（可选）</label><input id="project-publish" name="publish_command" placeholder="例如：./scripts/deploy-staging.sh"></div>
    <label class="checkbox"><input type="checkbox" name="auto_apply" value="true"> 审查和验证通过后自动提交到隔离分支（不会推送到远程）</label>
    <div class="form-actions"><button class="btn" type="button" data-action="close-modal">取消</button><button class="btn btn-primary" type="submit">创建项目</button></div>
  </form></div>`);
}
function showTokenModal(force = false) {
  if (!force && state.config && !state.config.auth_required) { toast('当前实例未启用访问令牌。'); return; }
  showModal(`<div class="modal-head"><div><h2>访问令牌</h2></div><button class="modal-close" data-action="close-modal">×</button></div><div class="modal-body"><form class="form" data-form="token"><div class="field"><label for="api-token">访问令牌</label><input id="api-token" name="token" type="password" placeholder="请输入访问令牌" value="${esc(state.token)}" autocomplete="off"><span class="hint">保存在浏览器本地存储中，仅发送到当前 FlowCodex 实例。</span></div><div class="form-actions"><button class="btn btn-primary" type="submit">保存并连接</button></div></form></div>`);
}

async function submitProject(form) {
  const values = Object.fromEntries(new FormData(form).entries());
  const payload = { name: values.name, repo_path: values.repo_path, default_branch: values.default_branch || 'main', verify_command: values.verify_command || '', publish_command: values.publish_command || '', allowed_paths: (values.allowed_paths || '.').split(',').map((item) => item.trim()).filter(Boolean), auto_apply: values.auto_apply === 'true' };
  try {
    const project = await api('/projects', { method: 'POST', body: JSON.stringify(payload) });
    closeModal();
    toast('项目已创建', 'success');
    state.projects.unshift(project);
    location.hash = `#/project/${project.id}`;
    await route();
  } catch (error) { toast(error.message, 'error'); }
}
async function submitRequirement(form) {
  const values = Object.fromEntries(new FormData(form).entries());
  try {
    const result = await api('/requirements', { method: 'POST', body: JSON.stringify({ project_id: values.project_id, title: values.title, raw_input: values.raw_input, start_run: true }) });
    toast('需求已提交，流水线已启动', 'success');
    const runId = result.run?.run?.id;
    if (runId) location.hash = `#/run/${runId}`;
    await route();
  } catch (error) { toast(error.message, 'error'); }
}
async function submitClarify(form) {
  const runId = form.dataset.id;
  const answers = {};
  new FormData(form).forEach((value, key) => { if (key.startsWith('q:') && String(value).trim()) answers[key.slice(2)] = String(value).trim(); });
  if (!Object.keys(answers).length) { toast('至少填写一个问题', 'error'); return; }
  try { await api(`/runs/${runId}/answers`, { method: 'POST', body: JSON.stringify({ answers }) }); toast('回答已提交，流水线继续', 'success'); await loadRun(runId); }
  catch (error) { toast(error.message, 'error'); }
}
async function runAction(action, id) {
  try {
    if (action === 'approve-plan') {
      await api(`/runs/${id}/approve-plan`, { method: 'POST', body: JSON.stringify({ approved: true }) });
      toast('方案已批准，进入编码', 'success');
    } else if (action === 'reject-plan') {
      const comment = window.prompt('驳回原因（会写入方案事件）', '方案需要调整');
      if (comment === null) return;
      await api(`/runs/${id}/approve-plan`, { method: 'POST', body: JSON.stringify({ approved: false, comment }) });
      toast('方案已驳回', 'error');
    } else if (action === 'override-feasibility') {
      const reason = window.prompt('人工放行原因', '已人工评估，接受当前风险');
      if (reason === null) return;
      await api(`/runs/${id}/override-feasibility`, { method: 'POST', body: JSON.stringify({ reason }) });
      toast('已人工放行', 'success');
    } else if (action === 'rework') {
      const note = window.prompt('返工说明：指出必须修复的问题', '请修复审查中的关键问题');
      if (note === null) return;
      await api(`/runs/${id}/rework`, { method: 'POST', body: JSON.stringify({ note }) });
      toast('已进入返工', 'success');
    } else if (action === 'cancel-run') {
      if (!window.confirm('取消本次运行？')) return;
      await api(`/runs/${id}/cancel`, { method: 'POST' });
      toast('运行已取消');
    } else if (action === 'promote') {
      await api(`/runs/${id}/promote`, { method: 'POST' });
      toast('已提交隔离分支', 'success');
    } else if (action === 'export-obsidian') {
      const result = await api(`/runs/${id}/export/obsidian`, { method: 'POST' });
      toast(`已导出：${result.path}`, 'success');
    }
    await loadRun(id, true);
  } catch (error) { toast(error.message, 'error'); }
}

async function downloadPatch(id) {
  try {
    const headers = state.token ? { Authorization: `Bearer ${state.token}` } : {};
    const response = await fetch(`/api/runs/${id}/artifact`, { headers });
    if (response.status === 401) {
      showTokenModal(true);
      throw new Error('需要访问令牌');
    }
    if (!response.ok) throw new Error(`请求失败（HTTP ${response.status}）`);
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `flowcodex-${id}.patch`;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
  } catch (error) { toast(`下载失败：${error.message}`, 'error'); }
}

async function feedbackKnowledge(id) {
  const reason = window.prompt('这条知识与实际代码/架构哪里不符？', '已与当前仓库不一致');
  if (reason === null) return;
  try {
    await api(`/knowledge/${id}/feedback`, { method: 'POST', body: JSON.stringify({ reason }) });
    toast('已记录反馈，置信度已下调', 'success');
    await loadKnowledge();
  } catch (error) { toast(error.message, 'error'); }
}

async function route() {
  state.activeProject = null;
  state.activeRun = null;
  const hash = location.hash.replace(/^#\/?/, '');
  if (hash.startsWith('run/')) { await loadRun(hash.slice(4)); return; }
  if (hash.startsWith('project/')) { await loadProject(hash.slice(8)); return; }
  if (hash === 'knowledge') { await loadKnowledge(); return; }
  await loadDashboard();
}

function handleClick(event) {
  const actionNode = event.target.closest('[data-action]');
  if (!actionNode) return;
  const action = actionNode.dataset.action;
  if (action === 'close-modal') { closeModal(); return; }
  if (action === 'new-project') { showProjectModal(); return; }
  if (action === 'show-token') { showTokenModal(true); return; }
  if (action === 'toggle-sidebar') { document.body.classList.toggle('sidebar-open'); return; }
  if (action === 'refresh') { route(); return; }
  if (action === 'set-tab') { state.runTab = actionNode.dataset.tab || 'overview'; if (state.activeRun) renderRunView(); return; }
  if (action === 'download-patch') { downloadPatch(actionNode.dataset.id); return; }
  if (action === 'feedback') { feedbackKnowledge(actionNode.dataset.id); return; }
  if (['approve-plan', 'reject-plan', 'override-feasibility', 'rework', 'cancel-run', 'promote', 'export-obsidian'].includes(action)) {
    runAction(action, actionNode.dataset.id);
  }
}

async function handleSubmit(event) {
  const form = event.target;
  const kind = form.dataset.form;
  if (!kind) return;
  event.preventDefault();
  if (kind === 'project') submitProject(form);
  if (kind === 'requirement') submitRequirement(form);
  if (kind === 'clarify') submitClarify(form);
  if (kind === 'token') {
    const token = new FormData(form).get('token')?.toString().trim() || '';
    const previousToken = state.token;
    state.token = token;
    try {
      const response = await fetch('/api/projects', { headers: token ? { Authorization: `Bearer ${token}` } : {} });
      if (response.status === 401) {
        state.token = previousToken;
        showTokenModal(true);
        toast('访问令牌无效，请重新输入', 'error');
        return;
      }
      if (!response.ok) throw new Error(`请求失败（HTTP ${response.status}）`);
      localStorage.setItem('flowcodex_token', token);
      closeModal();
      toast('访问令牌已保存', 'success');
      await route();
    } catch (error) {
      state.token = previousToken;
      showTokenModal(true);
      toast(`连接失败：${error.message}`, 'error');
    }
  }
}

async function bootstrap() {
  document.addEventListener('click', handleClick);
  document.addEventListener('submit', handleSubmit);
  document.addEventListener('keydown', (event) => { if (event.key === 'Escape' && state.modalOpen) closeModal(); });
  window.addEventListener('hashchange', () => route());
  try {
    state.config = await api('/config');
    if (state.config.auth_required && !state.token) showTokenModal(true);
    if (!state.config.auth_required || state.token) {
      try { state.projects = await api('/projects'); } catch {}
    }
    const health = await fetch('/api/health').then((response) => response.ok).catch(() => false);
    $('#health-dot')?.classList.toggle('offline', !health);
    renderSidebar('dashboard');
    await route();
  } catch (error) { errorView(error); }
}

bootstrap();
