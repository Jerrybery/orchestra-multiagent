// ── Orchestra Dashboard ─────────────────────────────────────────
// Git-graph style visualization: commits on left rail, ideas branch right, features branch from ideas

const STATUS_COLORS = {
  requirement:       '#da70d6',
  proposal_feature:  '#da70d680',
  idea:              '#8b949e',
  assigned:          '#58a6ff',
  in_progress:       '#d29922',
  implemented:       '#3fb950',
  testing:           '#bc8cff',
  review:            '#f0883e',
  accepted:          '#3fb950',
  done:              '#238636',
  rejected:          '#f85149',
};

// Branch colors for git graph (cycle through these)
const BRANCH_COLORS = ['#f97583','#79c0ff','#56d364','#d2a8ff','#ffa657','#ff7b72','#7ee787','#d29922'];

const SVG_NS = 'http://www.w3.org/2000/svg';

let graphData = { nodes: [], edges: [], branches: [], commits: [], proposals: [] };
let selectedNodeId = null;
let tooltipEl = null;

// ── Init tooltip element ────────────────────────────────────────
function initTooltip() {
  tooltipEl = document.createElement('div');
  tooltipEl.id = 'svg-tooltip';
  tooltipEl.style.cssText = `
    position:fixed; display:none; pointer-events:none; z-index:200;
    background:#1c2333; border:1px solid #30363d; border-radius:6px;
    padding:6px 10px; font-size:12px; color:#c9d1d9; max-width:350px;
    white-space:pre-wrap; box-shadow:0 4px 12px rgba(0,0,0,0.4);
  `;
  document.body.appendChild(tooltipEl);
}

function showTooltip(e, html) {
  tooltipEl.innerHTML = html;
  tooltipEl.style.display = 'block';
  tooltipEl.style.left = (e.clientX + 12) + 'px';
  tooltipEl.style.top = (e.clientY + 12) + 'px';
}

function hideTooltip() {
  tooltipEl.style.display = 'none';
}

// ── Fetch helpers ───────────────────────────────────────────────

async function fetchGraph() {
  const res = await fetch('/api/graph');
  graphData = await res.json();
  renderGraph();
}

async function fetchAgents() {
  const res = await fetch('/api/agents');
  const agents = await res.json();
  renderAgents(agents);
}

async function fetchTaskDetail(taskId) {
  const res = await fetch(`/api/tasks/${taskId}`);
  return await res.json();
}

async function fetchProposalDetail(proposalId) {
  const res = await fetch(`/api/proposals/${proposalId}`);
  return await res.json();
}

// ── Layout Constants ────────────────────────────────────────────

const COMMIT_Y_STEP = 32;
const COMMIT_X = 50;
const IDEA_OFFSET_X = 160;   // horizontal distance from commit rail to idea node
const FEAT_X = 440;          // X position of feature nodes — well to the right
const FEAT_ROW_H = 64;       // vertical spacing between feature rows
const PAD_TOP = 40;
const PAD_BOTTOM = 60;
const IDEA_R = 18;
const COMMIT_R = 6;
const FEAT_R = 7;
const TRUNK_X_OFFSET = 50;   // trunk line offset from idea right edge

// ── Build Layout ────────────────────────────────────────────────

function buildLayout() {
  const { commits, nodes, edges, proposals } = graphData;

  const positions = {};
  let maxX = 500, maxY = 200;

  // Assign colors to branches
  const branchColorMap = {};
  let colorIdx = 0;
  for (const c of commits) {
    for (const br of c.branches) {
      if (!branchColorMap[br]) {
        branchColorMap[br] = BRANCH_COLORS[colorIdx % BRANCH_COLORS.length];
        colorIdx++;
      }
    }
  }
  const defaultCommitColor = '#484f58';

  // ── Commit rail (left column) ──
  // git log returns newest first; reverse so oldest is at top, newest at bottom
  // (time flows downward, like a standard git graph)
  const commitsReversed = [...commits].reverse();
  const commitPositions = [];

  commitsReversed.forEach((c, i) => {
    const y = PAD_TOP + i * COMMIT_Y_STEP;
    const color = c.branches.length ? branchColorMap[c.branches[0]] : defaultCommitColor;
    commitPositions.push({ ...c, x: COMMIT_X, y, color });
    positions[`commit:${c.hash}`] = { x: COMMIT_X, y, type: 'commit', color, data: c };
    maxY = Math.max(maxY, y + 40);
  });

  // ── Find HEAD commit Y — Ideas branch from here ──
  // Use is_head flag from backend; fall back to the bottom commit
  const headCommit = commitPositions.find(c => c.is_head);
  const headY = headCommit ? headCommit.y
    : (commitPositions.length ? commitPositions[commitPositions.length - 1].y : PAD_TOP);
  let lastCommitY = headY;

  // ── Requirements (Ideas) and their features ──
  const reqs = nodes.filter(n => n.type === 'requirement');
  const tasks = nodes.filter(n => n.type === 'task');
  const propFeats = nodes.filter(n => n.type === 'proposal_feature');

  const allFeatures = [...tasks, ...propFeats];
  const reqGroups = {};
  for (const f of allFeatures) {
    const rid = f.requirement_id || '__none__';
    (reqGroups[rid] = reqGroups[rid] || []).push(f);
  }

  const ideaX = COMMIT_X + IDEA_OFFSET_X;

  // Running Y for feature placement — starts at the Idea Y
  let featCursorY = lastCommitY;

  reqs.forEach((req, reqIdx) => {
    const prop = proposals.find(p => p.requirement_id === req.id);
    const summary = prop?.summary || req.label;

    const features = reqGroups[req.id] || [];
    features.sort((a, b) => {
      const aDeps = (a.depends_on || []).length;
      const bDeps = (b.depends_on || []).length;
      return aDeps - bDeps || a.id.localeCompare(b.id);
    });

    // Idea node: at the last commit's Y (or after the previous idea's features)
    const ideaY = featCursorY;

    positions[req.id] = { x: ideaX, y: ideaY, type: 'requirement', data: { ...req, summary } };
    maxX = Math.max(maxX, ideaX + 100);

    // Features: stacked downward starting from ideaY
    features.forEach((feat, row) => {
      const fy = ideaY + row * FEAT_ROW_H;
      positions[feat.id] = { x: FEAT_X, y: fy, type: feat.type, data: feat, row };
      maxX = Math.max(maxX, FEAT_X + 220);
      maxY = Math.max(maxY, fy + 30);
    });

    // Next idea starts after all features of this one + gap
    const blockBottom = ideaY + Math.max(1, features.length) * FEAT_ROW_H;
    featCursorY = blockBottom + 40;
    maxY = Math.max(maxY, featCursorY);
  });

  // Orphan features (no requirement)
  const orphans = reqGroups['__none__'] || [];
  orphans.forEach((feat, row) => {
    const fy = blockY + row * FEAT_ROW_H;
    positions[feat.id] = { x: FEAT_X, y: fy, type: feat.type, data: feat, row };
    maxY = Math.max(maxY, fy + 30);
  });

  return { positions, commitPositions, branchColorMap,
           width: maxX + 80, height: maxY + PAD_BOTTOM };
}

// ── SVG Rendering ───────────────────────────────────────────────

function renderGraph() {
  const svg = document.getElementById('dag-svg');
  svg.innerHTML = '';

  const { nodes, edges, commits, proposals } = graphData;

  if (!nodes.length && !commits.length) {
    svg.setAttribute('width', 500);
    svg.setAttribute('height', 200);
    const text = svgEl('text', { x: 250, y: 100, 'text-anchor': 'middle',
      fill: '#8b949e', 'font-size': 14 });
    text.textContent = 'No features yet. Submit a requirement to get started.';
    svg.appendChild(text);
    return;
  }

  const layout = buildLayout();
  svg.setAttribute('width', layout.width);
  svg.setAttribute('height', layout.height);

  // ── Commit rail line (extends down to ideas) ──
  if (layout.commitPositions.length) {
    const first = layout.commitPositions[0];
    // Find the lowest Y of any idea node to extend the rail there
    const reqs = nodes.filter(n => n.type === 'requirement');
    const ideaYs = reqs.map(r => layout.positions[r.id]?.y).filter(Boolean);
    const railEndY = Math.max(
      layout.commitPositions[layout.commitPositions.length - 1].y,
      ...ideaYs
    );

    svg.appendChild(svgEl('line', {
      x1: COMMIT_X, y1: first.y, x2: COMMIT_X, y2: railEndY,
      stroke: '#30363d', 'stroke-width': 2, opacity: 0.6,
    }));
  }

  // ── Commit parent edges ──
  for (const c of commits) {
    const pos = layout.positions[`commit:${c.hash}`];
    if (!pos) continue;
    for (const parentHash of (c.parents || [])) {
      const parentPos = layout.positions[`commit:${parentHash}`];
      if (parentPos) {
        svg.appendChild(svgEl('line', {
          x1: pos.x, y1: pos.y, x2: parentPos.x, y2: parentPos.y,
          stroke: '#30363d', 'stroke-width': 2, opacity: 0.4,
        }));
      }
    }
  }

  // ── Commit nodes (ring style, hover for details) ──
  for (const c of layout.commitPositions) {
    const g = svgEl('g', { transform: `translate(${c.x},${c.y})`, class: 'dag-node' });
    const isHead = c.is_head;

    // Outer ring — bigger and brighter for HEAD
    g.appendChild(svgEl('circle', {
      r: isHead ? COMMIT_R + 4 : COMMIT_R + 2,
      fill: 'none',
      stroke: isHead ? '#f0883e' : '#fff',
      'stroke-width': isHead ? 2.5 : 1.5,
      opacity: isHead ? 1 : 0.7,
    }));
    // Inner filled circle
    g.appendChild(svgEl('circle', { r: COMMIT_R, fill: c.color }));

    // Branch label (small, always visible if it has a branch ref)
    if (c.branches.length) {
      for (let i = 0; i < c.branches.length; i++) {
        const tag = svgEl('g', { transform: `translate(${COMMIT_R + 8}, ${-6 + i * 14})` });
        const brName = c.branches[i];
        const textEl = svgEl('text', {
          'font-size': 10, fill: layout.branchColorMap[brName] || '#8b949e',
          'font-family': 'monospace',
        });
        textEl.textContent = brName;
        // Background pill
        tag.appendChild(textEl);
        g.appendChild(tag);
      }
    }

    // Hover tooltip
    g.addEventListener('mouseenter', (e) => {
      const refs = c.branches.length ? `<br><b>${c.branches.join(', ')}</b>` : '';
      showTooltip(e, `<b>${c.short}</b> ${esc(c.message)}${refs}<br><span style="opacity:0.6">${c.author} · ${c.date}</span>`);
    });
    g.addEventListener('mousemove', (e) => {
      tooltipEl.style.left = (e.clientX + 12) + 'px';
      tooltipEl.style.top = (e.clientY + 12) + 'px';
    });
    g.addEventListener('mouseleave', hideTooltip);

    svg.appendChild(g);
  }

  // ── Idea blocks: connector line from rail, tree branches to features ──
  const reqs = nodes.filter(n => n.type === 'requirement');
  const allFeatures = nodes.filter(n => n.type === 'task' || n.type === 'proposal_feature');

  for (const req of reqs) {
    const iPos = layout.positions[req.id];
    if (!iPos) continue;

    // Dashed line from commit rail to idea
    svg.appendChild(svgEl('line', {
      x1: COMMIT_X, y1: iPos.y, x2: iPos.x - IDEA_R, y2: iPos.y,
      stroke: '#da70d6', 'stroke-width': 1.5, 'stroke-dasharray': '6 3', opacity: 0.5,
    }));

    // Tree connector: L-shaped lines from idea to each feature
    // Trunk X is midway between idea right edge and feature left edge
    const myFeats = allFeatures.filter(f => f.requirement_id === req.id);
    if (myFeats.length) {
      const trunkX = FEAT_X - 30; // trunk line is 30px left of feature nodes
      const featYs = myFeats.map(f => layout.positions[f.id]?.y).filter(Boolean);
      if (featYs.length) {
        const minFY = Math.min(...featYs);
        const maxFY = Math.max(...featYs);

        // Horizontal from idea right edge to trunk
        svg.appendChild(svgEl('line', {
          x1: iPos.x + IDEA_R, y1: iPos.y, x2: trunkX, y2: iPos.y,
          stroke: '#484f58', 'stroke-width': 1.5, opacity: 0.4,
        }));

        // Vertical trunk (connecting all feature branch points)
        const trunkTop = Math.min(iPos.y, minFY);
        const trunkBot = maxFY;
        svg.appendChild(svgEl('line', {
          x1: trunkX, y1: trunkTop, x2: trunkX, y2: trunkBot,
          stroke: '#484f58', 'stroke-width': 1.5, opacity: 0.4,
        }));

        // Short horizontal branch from trunk to each feature node
        for (const feat of myFeats) {
          const fPos = layout.positions[feat.id];
          if (!fPos) continue;
          const isProposal = feat.type === 'proposal_feature';
          svg.appendChild(svgEl('line', {
            x1: trunkX, y1: fPos.y, x2: fPos.x - FEAT_R - 4, y2: fPos.y,
            stroke: isProposal ? '#da70d680' : '#484f58',
            'stroke-width': 1.5,
            'stroke-dasharray': isProposal ? '4 3' : 'none',
            opacity: 0.5,
          }));
        }
      }
    }
  }

  // ── Dependency edges — only shown for the selected node ──
  // When a feature is selected, show its deps (what it depends on) and
  // reverse deps (what depends on it) as curved arrows on the left side.
  if (selectedNodeId) {
    const depEdges = edges.filter(e => e.type === 'dependency' || e.type === 'proposal_dep');
    // Filter: only edges touching the selected node
    const relevant = depEdges.filter(e => e.from === selectedNodeId || e.to === selectedNodeId);
    const depLaneX = FEAT_X - 18;

    for (let i = 0; i < relevant.length; i++) {
      const edge = relevant[i];
      const from = layout.positions[edge.from];
      const to = layout.positions[edge.to];
      if (!from || !to) continue;

      const isProposal = edge.type === 'proposal_dep';
      const color = isProposal ? '#da70d680' : '#58a6ff';
      const dash = isProposal ? '4 3' : 'none';

      const fx = from.x - FEAT_R - 2;
      const fy = from.y;
      const tx = to.x - FEAT_R - 2;
      const ty = to.y;
      const curveOffset = depLaneX - 16 - i * 14;

      svg.appendChild(svgEl('path', {
        d: `M${fx},${fy} C${curveOffset},${fy} ${curveOffset},${ty} ${tx},${ty}`,
        fill: 'none', stroke: color, 'stroke-width': 2,
        'stroke-dasharray': dash, opacity: 0.7,
      }));

      // Arrow head
      svg.appendChild(svgEl('path', {
        d: `M${tx},${ty} L${tx - 6},${ty - 4} M${tx},${ty} L${tx - 6},${ty + 4}`,
        fill: 'none', stroke: color, 'stroke-width': 2, opacity: 0.7,
      }));
    }
  }

  // ── Idea nodes (large) ──
  for (const req of reqs) {
    const pos = layout.positions[req.id];
    if (!pos) continue;

    const g = svgEl('g', {
      transform: `translate(${pos.x},${pos.y})`,
      class: `dag-node${req.id === selectedNodeId ? ' selected' : ''}`,
    });

    g.appendChild(svgEl('circle', {
      r: IDEA_R, fill: '#161b22', stroke: '#da70d6', 'stroke-width': 2.5,
      class: 'node-circle',
    }));

    const inner = svgEl('text', {
      'text-anchor': 'middle', dy: 4, 'font-size': 9, fill: '#da70d6',
      'font-weight': 'bold', 'letter-spacing': '1px',
    });
    inner.textContent = 'IDEA';
    g.appendChild(inner);

    const hasPending = proposals.some(p => p.requirement_id === req.id && p.status === 'pending');
    if (hasPending) {
      g.appendChild(svgEl('circle', { cx: IDEA_R - 2, cy: -(IDEA_R - 2), r: 6, fill: '#f0883e' }));
      const bang = svgEl('text', {
        x: IDEA_R - 2, y: -(IDEA_R - 6), 'text-anchor': 'middle',
        'font-size': 9, fill: '#fff', 'font-weight': 'bold',
      });
      bang.textContent = '!';
      g.appendChild(bang);
    }

    const summary = pos.data?.summary || req.label;
    g.addEventListener('mouseenter', (e) => showTooltip(e, `<b>Idea:</b> ${esc(summary)}`));
    g.addEventListener('mousemove', (e) => { tooltipEl.style.left = (e.clientX+12)+'px'; tooltipEl.style.top = (e.clientY+12)+'px'; });
    g.addEventListener('mouseleave', hideTooltip);
    g.addEventListener('click', () => onNodeClick(req));
    svg.appendChild(g);
  }

  // ── Feature nodes ──
  for (const feat of allFeatures) {
    const pos = layout.positions[feat.id];
    if (!pos) continue;

    const g = svgEl('g', {
      transform: `translate(${pos.x},${pos.y})`,
      class: `dag-node${feat.id === selectedNodeId ? ' selected' : ''}`,
    });

    if (feat.type === 'proposal_feature') {
      g.appendChild(svgEl('circle', {
        r: FEAT_R, fill: 'none', stroke: '#da70d680',
        'stroke-width': 2, 'stroke-dasharray': '4 3',
      }));
    } else {
      g.appendChild(svgEl('circle', {
        r: FEAT_R + 2, fill: 'none', stroke: '#fff', 'stroke-width': 1.5, opacity: 0.5,
      }));
      g.appendChild(svgEl('circle', {
        r: FEAT_R, fill: STATUS_COLORS[feat.status] || '#8b949e', class: 'node-circle',
      }));
    }

    // ID + title on the right of the node
    const idLabel = svgEl('text', {
      x: FEAT_R + 6, dy: -3, 'font-size': 10, fill: '#8b949e', 'font-family': 'monospace',
    });
    idLabel.textContent = feat.id;
    g.appendChild(idLabel);

    const titleLabel = svgEl('text', { x: FEAT_R + 6, dy: 11, 'font-size': 11, fill: '#c9d1d9' });
    titleLabel.textContent = truncate(feat.label, 24);
    g.appendChild(titleLabel);

    const statusText = svgEl('text', {
      x: FEAT_R + 6, dy: 24, 'font-size': 9,
      fill: feat.type === 'proposal_feature' ? '#f0883e' : (STATUS_COLORS[feat.status] || '#8b949e'),
    });
    let sLabel = feat.type === 'proposal_feature' ? 'pending review' : (feat.status || '').replace('_', ' ');
    if (feat.assigned_to) sLabel += ` (${feat.assigned_to})`;
    statusText.textContent = sLabel;
    g.appendChild(statusText);

    // Dependency indicator (small left-side marker)
    const depCount = (feat.depends_on || []).length;
    if (depCount > 0) {
      const depInd = svgEl('text', {
        x: -(FEAT_R + 6), dy: 4, 'text-anchor': 'end',
        'font-size': 9, fill: '#58a6ff', opacity: 0.7,
      });
      depInd.textContent = `${depCount} dep${depCount > 1 ? 's' : ''}`;
      g.appendChild(depInd);
    }

    g.addEventListener('mouseenter', (e) => {
      const s = feat.type === 'proposal_feature' ? 'Proposed' : feat.status;
      const deps = depCount ? `<br>Deps: ${feat.depends_on.join(', ')}` : '';
      showTooltip(e, `<b>${feat.id}:</b> ${esc(feat.label)}<br>Status: ${s}${deps}<br><i>Click to show dependency arrows</i>`);
    });
    g.addEventListener('mousemove', (e) => { tooltipEl.style.left = (e.clientX+12)+'px'; tooltipEl.style.top = (e.clientY+12)+'px'; });
    g.addEventListener('mouseleave', hideTooltip);
    g.addEventListener('click', () => onNodeClick(feat));
    svg.appendChild(g);
  }
}

// ── SVG helpers ─────────────────────────────────────────────────

function svgEl(tag, attrs = {}) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v !== undefined && v !== null) el.setAttribute(k, v);
  }
  return el;
}

function truncate(s, n) {
  return s.length > n ? s.slice(0, n) + '...' : s;
}

function esc(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

// ── Node click → detail panel ───────────────────────────────────

async function onNodeClick(node) {
  selectedNodeId = node.id;
  renderGraph();

  const titleEl = document.getElementById('detail-title');
  const contentEl = document.getElementById('detail-content');

  if (node.type === 'requirement') {
    titleEl.textContent = `Idea: ${node.id}`;
    const pendingProps = graphData.proposals.filter(
      p => p.requirement_id === node.id && p.status === 'pending'
    );

    let html = `
      <div class="detail-section">
        <h3>Original Requirement</h3>
        <div class="markdown-body">${marked.parse(node.content || '')}</div>
      </div>
    `;

    if (pendingProps.length) {
      for (const prop of pendingProps) {
        html += await renderProposalReview(prop.id);
      }
    }

    contentEl.innerHTML = html;
    return;
  }

  if (node.type === 'proposal_feature') {
    titleEl.textContent = `Proposed: ${node.label}`;
    const prop = graphData.proposals.find(p => p.id === node.proposal_id);
    let html = `<div class="detail-section">
      <h3>This feature is part of a proposal awaiting your review</h3>
      <p style="color:var(--text-dim)">Proposal: ${node.proposal_id}</p>
    </div>`;
    if (prop) html += await renderProposalReview(prop.id);
    contentEl.innerHTML = html;
    return;
  }

  // Task
  titleEl.textContent = `Loading...`;
  const task = await fetchTaskDetail(node.id);
  titleEl.textContent = `${task.id}: ${task.title}`;

  let html = '';

  html += `<div class="detail-section">
    <h3>Info</h3>
    <dl class="detail-meta">
      <dt>Status</dt>
      <dd><span class="status-badge" style="background:${STATUS_COLORS[task.status]}20;color:${STATUS_COLORS[task.status]}">${task.status}</span></dd>
      <dt>Branch</dt><dd>${task.branch || '-'}</dd>
      <dt>Agent</dt><dd>${task.assigned_to || '-'}</dd>
      <dt>Dependencies</dt><dd>${task.depends_on.length ? task.depends_on.join(', ') : 'None'}</dd>
    </dl>
  </div>`;

  if (task.requirement) {
    html += `<div class="detail-section">
      <h3>Origin Idea</h3>
      <div class="markdown-body">${marked.parse(task.requirement.content || '')}</div>
    </div>`;
  }

  if (task.spec) {
    html += `<div class="detail-section">
      <h3>Feature Spec (by Head Leader)</h3>
      <div class="markdown-body">${marked.parse(task.spec)}</div>
    </div>`;
  }

  if (task.report) {
    html += `<div class="detail-section">
      <h3>Verification Report (by Feature Interpreter)</h3>
      <div class="markdown-body">${marked.parse(task.report)}</div>
    </div>`;
  }

  if (task.reject_reason) {
    html += `<div class="detail-section">
      <h3>Rejection Reason</h3>
      <div class="markdown-body" style="border-color:var(--c-rejected)">${marked.parse(task.reject_reason)}</div>
    </div>`;
  }

  if (task.status === 'review') {
    html += `<div class="detail-section">
      <h3>Review Actions</h3>
      <div class="review-actions">
        <button class="btn btn-primary" onclick="reviewTask('${task.id}', 'accept')">Accept & Merge</button>
        <button class="btn btn-reject" onclick="showReject('${task.id}')">Reject</button>
      </div>
      <div id="reject-form-${task.id}" style="display:none;margin-top:8px">
        <textarea id="reject-reason-${task.id}" rows="3" placeholder="Rejection reason..."></textarea>
        <button class="btn btn-reject" style="margin-top:6px" onclick="reviewTask('${task.id}', 'reject')">Confirm Reject</button>
      </div>
    </div>`;
  }

  contentEl.innerHTML = html;
}

// ── Proposal Review Panel ───────────────────────────────────────

async function renderProposalReview(proposalId) {
  const prop = await fetchProposalDetail(proposalId);

  let html = `<div class="detail-section proposal-review">
    <h3>Proposal Review: ${proposalId}</h3>
    <p style="color:var(--text-dim);margin-bottom:12px">
      Head Leader produced ${prop.features.length} features. Review and approve/reject before they enter the pipeline.
    </p>
    <div class="proposal-features">`;

  for (const feat of prop.features) {
    const deps = feat.depends_on?.length ? ` (deps: ${feat.depends_on.join(', ')})` : '';
    html += `<div class="proposal-feat-item">
      <label>
        <input type="checkbox" class="prop-feat-cb" data-feat-id="${feat.id}" checked>
        <strong>${feat.id}</strong>: ${feat.title}${deps}
      </label>`;
    if (feat.spec) {
      html += `<details style="margin:6px 0 6px 24px">
        <summary style="cursor:pointer;color:var(--accent);font-size:12px">View Spec</summary>
        <div class="markdown-body" style="margin-top:6px">${marked.parse(feat.spec)}</div>
      </details>`;
    }
    html += `</div>`;
  }

  html += `</div>
    <div style="display:flex;gap:8px;margin-top:12px">
      <button class="btn btn-primary" onclick="approveProposal('${proposalId}')">Approve Selected</button>
      <button class="btn" onclick="requestResplit('${proposalId}')">Request Re-split</button>
      <button class="btn btn-reject" onclick="abandonIdea('${proposalId}')">Abandon Idea</button>
    </div>
  </div>`;

  return html;
}

async function approveProposal(proposalId) {
  const checkboxes = document.querySelectorAll('.prop-feat-cb');
  const featureIds = [];
  checkboxes.forEach(cb => { if (cb.checked) featureIds.push(cb.dataset.featId); });

  await fetch(`/api/proposals/${proposalId}/review`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action: 'approve', feature_ids: featureIds }),
  });
  addLogEntry('proposal_approved', `Approved ${featureIds.length} features from ${proposalId}`);
  await fetchGraph();
}

async function abandonIdea(proposalId) {
  await fetch(`/api/proposals/${proposalId}/review`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action: 'reject' }),
  });
  addLogEntry('idea_abandoned', `Abandoned proposal ${proposalId}`);
  await fetchGraph();
}

function requestResplit(proposalId) {
  // TODO: re-submit to HL with feedback
  addLogEntry('resplit_requested', `Re-split requested for ${proposalId} (not yet implemented)`);
}

function showReject(taskId) {
  document.getElementById(`reject-form-${taskId}`).style.display = 'block';
}

async function reviewTask(taskId, action) {
  const body = { action };
  if (action === 'reject') {
    body.reason = document.getElementById(`reject-reason-${taskId}`).value || 'No reason given';
  }
  await fetch(`/api/tasks/${taskId}/review`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  await fetchGraph();
  const node = graphData.nodes.find(n => n.id === taskId);
  if (node) onNodeClick(node);
}

// ── Agent status bar ────────────────────────────────────────────

let cachedAgents = [];

function renderAgents(agents) {
  cachedAgents = agents;
  const bar = document.getElementById('agent-status');
  const running = agents.filter(a => a.state === 'running');
  if (!running.length) {
    bar.innerHTML = '<span class="agent-badge">No agents running</span>';
    return;
  }
  bar.innerHTML = running.map(a => {
    const elapsed = Math.round(a.elapsed);
    return `<span class="agent-badge running" onclick="showAgentDetail('${a.agent_id}')">${a.role.replace('_',' ')} → ${a.task_id || '...'} (${elapsed}s)</span>`;
  }).join('');
}

// ── Agent detail panel (right panel) ────────────────────────────

document.getElementById('btn-show-agents').addEventListener('click', showAgentsPanel);

async function showAgentsPanel() {
  const titleEl = document.getElementById('detail-title');
  const contentEl = document.getElementById('detail-content');
  titleEl.textContent = 'Agents';

  const res = await fetch('/api/agents');
  const agents = await res.json();

  if (!agents.length) {
    contentEl.innerHTML = '<div class="empty-state">No agents have been spawned yet</div>';
    return;
  }

  let html = '';
  for (const a of agents) {
    const stateColor = a.state === 'running' ? 'var(--c-in_progress)' :
                       a.state === 'finished' ? 'var(--c-done)' : 'var(--c-rejected)';
    const elapsed = Math.round(a.elapsed);
    html += `<div class="agent-list-item" onclick="showAgentDetail('${a.agent_id}')">
      <div class="agent-header">
        <span><b>${a.agent_id}</b> → ${a.task_id || '-'}</span>
        <span style="color:${stateColor}">${a.state} (${elapsed}s)</span>
      </div>
      <div style="font-size:11px;color:var(--text-dim)">${a.role} · ${a.log_line_count} log lines</div>
    </div>`;
  }
  contentEl.innerHTML = html;
}

async function showAgentDetail(agentId) {
  const titleEl = document.getElementById('detail-title');
  const contentEl = document.getElementById('detail-content');
  titleEl.textContent = `Agent: ${agentId}`;

  const res = await fetch(`/api/agents/${agentId}/logs`);
  const data = await res.json();

  let html = `<div class="detail-section">
    <h3>Status: <span style="color:${data.state === 'running' ? 'var(--c-in_progress)' : data.state === 'finished' ? 'var(--c-done)' : 'var(--c-rejected)'}">${data.state}</span></h3>
    <p style="color:var(--text-dim);font-size:12px">${data.total} log lines</p>
  </div>
  <div class="detail-section">
    <h3>Live Output</h3>
    <div class="agent-log-viewer" id="agent-log-viewer-${agentId}">`;

  for (const line of data.lines) {
    html += `<div class="agent-log-line">${esc(line)}</div>`;
  }

  html += `</div></div>`;

  // Auto-refresh button for running agents
  if (data.state === 'running') {
    html += `<button class="btn" style="margin-top:8px" onclick="showAgentDetail('${agentId}')">Refresh</button>`;
  }

  contentEl.innerHTML = html;

  // Scroll to bottom
  const viewer = document.getElementById(`agent-log-viewer-${agentId}`);
  if (viewer) viewer.scrollTop = viewer.scrollHeight;
}

// ── Submit modal ────────────────────────────────────────────────

document.getElementById('btn-submit').addEventListener('click', () => {
  document.getElementById('modal-overlay').classList.remove('hidden');
  document.getElementById('input-requirement').focus();
});

document.getElementById('btn-cancel').addEventListener('click', () => {
  document.getElementById('modal-overlay').classList.add('hidden');
});

document.getElementById('btn-do-submit').addEventListener('click', async () => {
  const text = document.getElementById('input-requirement').value.trim();
  if (!text) return;

  await fetch('/api/submit', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ requirement: text }),
  });

  document.getElementById('modal-overlay').classList.add('hidden');
  document.getElementById('input-requirement').value = '';
  addLogEntry('submit', 'Requirement submitted to Head Leader');
});

// ── Log tabs ────────────────────────────────────────────────────

document.querySelectorAll('.log-tab').forEach(tab => {
  tab.addEventListener('click', () => switchTab(tab.dataset.tab));
});

function switchTab(tabName) {
  document.querySelectorAll('.log-tab').forEach(t => t.classList.toggle('active', t.dataset.tab === tabName));
  document.querySelectorAll('.log-tab-content').forEach(c => c.classList.toggle('active', c.id === `tab-${tabName}`));
}

// ── SSE Event stream ────────────────────────────────────────────

function connectSSE() {
  const evtSource = new EventSource('/api/events/stream');

  evtSource.addEventListener('orchestra', (e) => {
    const { event, data } = JSON.parse(e.data);

    if (event === 'agent_log') {
      // Push to agent log tab instead of main event log
      addAgentLogLine(data.agent_id, data.stream, data.line);
      return;
    }

    addLogEntry(event, JSON.stringify(data));
    if (['hl_done','hl_failed','fr_done','fi_done','task_done','task_rejected',
         'tasks_promoted','fr_start','fi_start','proposal_approved','proposal_rejected',
         ].includes(event)) {
      fetchGraph();
      fetchAgents();
    }
  });

  evtSource.onerror = () => {
    setTimeout(connectSSE, 3000);
  };
}

function addLogEntry(event, msg) {
  const el = document.getElementById('log-entries');
  const time = new Date().toLocaleTimeString();
  const div = document.createElement('div');
  div.className = 'log-entry';
  div.innerHTML = `<span class="log-time">${time}</span> <span class="log-event">${event}</span> ${msg}`;
  el.appendChild(div);
  el.scrollTop = el.scrollHeight;
}

function addAgentLogLine(agentId, stream, line) {
  const el = document.getElementById('agent-log-entries');
  const div = document.createElement('div');
  div.className = 'agent-log-line';
  const streamClass = stream === 'stderr' ? ' stream-stderr' : '';
  div.innerHTML = `<span class="agent-tag">${agentId}</span><span class="${streamClass}">${esc(line)}</span>`;
  el.appendChild(div);

  // Keep max 1000 lines
  while (el.childElementCount > 1000) el.removeChild(el.firstChild);
  el.scrollTop = el.scrollHeight;

  // Also update the inline viewer if it's open for this agent
  const viewer = document.getElementById(`agent-log-viewer-${agentId}`);
  if (viewer) {
    const logDiv = document.createElement('div');
    logDiv.className = 'agent-log-line';
    logDiv.textContent = `[${stream}] ${line}`;
    viewer.appendChild(logDiv);
    viewer.scrollTop = viewer.scrollHeight;
  }
}

// ── Setup Screen (project selector) ─────────────────────────────

async function setupBrowse(path) {
  const res = await fetch(`/api/browse?path=${encodeURIComponent(path)}`);
  if (!res.ok) {
    const err = await res.json();
    alert(err.detail || 'Cannot browse path');
    return;
  }
  const data = await res.json();
  const browser = document.getElementById('setup-browser');
  const pathInput = document.getElementById('setup-path');
  pathInput.value = data.current;

  let html = '';
  // Parent directory
  if (data.current !== data.parent) {
    html += `<div class="dir-entry" onclick="setupBrowse('${esc(data.parent)}')">
      <span class="dir-icon">📁</span> <b>..</b>
    </div>`;
  }

  for (const entry of data.entries) {
    const badges = [];
    if (entry.is_git) badges.push('<span class="dir-badge dir-badge-git">git</span>');
    if (entry.has_orchestra) badges.push('<span class="dir-badge dir-badge-orch">orchestra</span>');
    html += `<div class="dir-entry" onclick="setupSelectDir('${esc(entry.path)}', ${entry.is_git}, ${entry.has_orchestra})">
      <span class="dir-icon">📁</span>
      <span>${esc(entry.name)}</span>
      <span class="dir-badges">${badges.join('')}</span>
    </div>`;
  }

  if (!data.entries.length) {
    html += '<div style="padding:12px;color:var(--text-dim);text-align:center">No subdirectories</div>';
  }

  browser.innerHTML = html;
}

function setupSelectDir(path, isGit, hasOrchestra) {
  document.getElementById('setup-path').value = path;

  const info = document.getElementById('setup-info');
  info.classList.remove('hidden', 'info-ok', 'info-warn');

  if (hasOrchestra) {
    info.classList.add('info-ok');
    info.textContent = 'This project already has Orchestra initialized. Connecting...';
    document.getElementById('btn-setup-init').textContent = 'Connect to Project';
  } else if (isGit) {
    info.classList.add('info-ok');
    info.textContent = 'Git repository detected. Orchestra will be initialized here.';
    document.getElementById('btn-setup-init').textContent = 'Initialize Orchestra';
  } else {
    info.classList.add('info-warn');
    info.textContent = 'Not a git repository. A new repo will be created.';
    document.getElementById('btn-setup-init').textContent = 'Initialize Orchestra';
  }
}

async function setupInit() {
  const path = document.getElementById('setup-path').value.trim();
  if (!path) return;

  const btn = document.getElementById('btn-setup-init');
  btn.disabled = true;
  btn.textContent = 'Initializing...';

  try {
    const res = await fetch('/api/init', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project_path: path }),
    });

    if (!res.ok) {
      const err = await res.json();
      alert(err.detail || 'Initialization failed');
      btn.disabled = false;
      btn.textContent = 'Initialize Orchestra';
      return;
    }

    const data = await res.json();
    showDashboard(data.project_path);
  } catch (e) {
    alert('Error: ' + e.message);
    btn.disabled = false;
    btn.textContent = 'Initialize Orchestra';
  }
}

function showDashboard(projectPath) {
  document.getElementById('setup-screen').classList.add('hidden');
  document.getElementById('dashboard').classList.remove('hidden');
  if (projectPath) {
    document.getElementById('project-label').textContent = projectPath;
  }
  // Start dashboard polling
  startDashboard();
}

function startDashboard() {
  initTooltip();
  fetchGraph();
  fetchAgents();
  setInterval(fetchAgents, 5000);
  setInterval(fetchGraph, 10000);
  connectSSE();
}

// ── Startup: check if already initialized ───────────────────────

(async function boot() {
  const res = await fetch('/api/status');
  const data = await res.json();
  if (data.initialized) {
    showDashboard(data.project_path);
  } else {
    document.getElementById('setup-screen').classList.remove('hidden');
    // Auto-browse home directory
    setupBrowse('~');
  }
})();
