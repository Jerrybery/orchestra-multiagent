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

const COMMIT_Y_STEP = 28;
const COMMIT_X_BASE = 50;    // leftmost lane X
const LANE_WIDTH = 24;       // horizontal distance between branch lanes
const IDEA_OFFSET_X = 120;   // horizontal distance from rightmost lane to idea node
const FEAT_ROW_H = 52;       // vertical spacing between feature rows
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

  // Assign colors to branches and determine lane assignments
  const branchColorMap = {};
  const branchLaneMap = {};
  let colorIdx = 0;
  let laneIdx = 0;

  // First pass: collect all branch names, assign main/master to lane 0
  const allBranches = new Set();
  for (const c of commits) {
    for (const br of c.branches) allBranches.add(br);
  }
  // Assign main/master first
  for (const br of ['main', 'master']) {
    if (allBranches.has(br)) {
      branchLaneMap[br] = 0;
      branchColorMap[br] = BRANCH_COLORS[colorIdx % BRANCH_COLORS.length];
      colorIdx++;
      laneIdx = 1;
      allBranches.delete(br);
      break;
    }
  }
  // Remaining branches get subsequent lanes
  for (const br of allBranches) {
    if (branchLaneMap[br] === undefined) {
      branchLaneMap[br] = laneIdx++;
      branchColorMap[br] = BRANCH_COLORS[colorIdx % BRANCH_COLORS.length];
      colorIdx++;
    }
  }
  const numLanes = Math.max(laneIdx, 1);
  const defaultCommitColor = '#484f58';

  // Helper: get lane X for a given lane index
  function laneX(lane) {
    return COMMIT_X_BASE + lane * LANE_WIDTH;
  }

  // Build a map from commit hash to its branch (for lane assignment)
  // Strategy: assign each commit to the lane of its first branch ref,
  // or inherit from its child commit if no branch ref
  const commitsReversed = [...commits].reverse();
  const commitLaneAssignment = {};

  // First: assign commits that have branch refs
  for (const c of commitsReversed) {
    if (c.branches.length) {
      commitLaneAssignment[c.hash] = branchLaneMap[c.branches[0]] ?? 0;
    }
  }

  // Second pass (newest to oldest): propagate lane from child to parent
  // Walk forward (newest first) since children know their lane
  for (const c of commits) {
    if (commitLaneAssignment[c.hash] === undefined) {
      commitLaneAssignment[c.hash] = 0; // default to main lane
    }
    // Propagate to parents that don't have assignments yet
    for (const ph of (c.parents || [])) {
      if (commitLaneAssignment[ph] === undefined) {
        commitLaneAssignment[ph] = commitLaneAssignment[c.hash];
      }
    }
  }

  // ── Commit positions ──
  const commitPositions = [];

  commitsReversed.forEach((c, i) => {
    const y = PAD_TOP + i * COMMIT_Y_STEP;
    const lane = commitLaneAssignment[c.hash] ?? 0;
    const x = laneX(lane);
    const color = c.branches.length ? branchColorMap[c.branches[0]] : defaultCommitColor;
    const isMerge = (c.parents || []).length > 1;
    commitPositions.push({ ...c, x, y, color, lane, isMerge });
    positions[`commit:${c.hash}`] = { x, y, type: 'commit', color, data: c, lane, isMerge };
    maxY = Math.max(maxY, y + 40);
  });

  // ── Find HEAD commit Y — Ideas branch from here ──
  const headCommit = commitPositions.find(c => c.is_head);
  const headY = headCommit ? headCommit.y
    : (commitPositions.length ? commitPositions[commitPositions.length - 1].y : PAD_TOP);
  let lastCommitY = headY;

  // ── Dynamic FEAT_X based on number of lanes ──
  const rightmostLaneX = laneX(numLanes - 1);
  const ideaX = rightmostLaneX + IDEA_OFFSET_X;
  const featX = ideaX + IDEA_R + 100; // dynamic feature X based on idea position

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
      positions[feat.id] = { x: featX, y: fy, type: feat.type, data: feat, row };
      maxX = Math.max(maxX, featX + 220);
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
    const fy = featCursorY + row * FEAT_ROW_H;
    positions[feat.id] = { x: featX, y: fy, type: feat.type, data: feat, row };
    maxY = Math.max(maxY, fy + 30);
  });

  return { positions, commitPositions, branchColorMap, branchLaneMap,
           numLanes, featX, ideaX,
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

  // ── Faint grid tick marks on left margin for visual rhythm ──
  if (layout.commitPositions.length) {
    const firstY = layout.commitPositions[0].y;
    const lastY = layout.commitPositions[layout.commitPositions.length - 1].y;
    for (let ty = firstY; ty <= lastY; ty += COMMIT_Y_STEP) {
      svg.appendChild(svgEl('line', {
        x1: 8, y1: ty, x2: 18, y2: ty,
        stroke: '#30363d', 'stroke-width': 1, opacity: 0.3,
      }));
    }
  }

  // ── Per-branch trunk lines ──
  if (layout.commitPositions.length) {
    // Group commits by lane, draw a trunk line per branch lane
    const laneCommits = {};
    for (const c of layout.commitPositions) {
      (laneCommits[c.lane] = laneCommits[c.lane] || []).push(c);
    }
    // Find the lowest Y of any idea node to extend main trunk there
    const reqs = nodes.filter(n => n.type === 'requirement');
    const ideaYs = reqs.map(r => layout.positions[r.id]?.y).filter(Boolean);

    for (const [lane, lcs] of Object.entries(laneCommits)) {
      if (lcs.length < 1) continue;
      const ys = lcs.map(c => c.y);
      let topY = Math.min(...ys);
      let botY = Math.max(...ys);
      // Extend main lane (0) down to idea nodes if needed
      if (parseInt(lane) === 0 && ideaYs.length) {
        botY = Math.max(botY, ...ideaYs);
      }
      const lx = lcs[0].x;
      const branchColor = lcs[0].color || '#30363d';
      svg.appendChild(svgEl('line', {
        x1: lx, y1: topY, x2: lx, y2: botY,
        stroke: branchColor, 'stroke-width': 2, opacity: 0.35,
      }));
    }
  }

  // ── Commit parent edges (curved bezier for cross-lane, straight for same-lane) ──
  for (const c of commits) {
    const pos = layout.positions[`commit:${c.hash}`];
    if (!pos) continue;
    for (const parentHash of (c.parents || [])) {
      const parentPos = layout.positions[`commit:${parentHash}`];
      if (parentPos) {
        if (pos.lane === parentPos.lane) {
          // Same lane: straight line
          svg.appendChild(svgEl('line', {
            x1: pos.x, y1: pos.y, x2: parentPos.x, y2: parentPos.y,
            stroke: pos.color || '#30363d', 'stroke-width': 2, opacity: 0.4,
          }));
        } else {
          // Cross-lane: cubic bezier curve
          const midY = (pos.y + parentPos.y) / 2;
          svg.appendChild(svgEl('path', {
            d: `M${pos.x},${pos.y} C${pos.x},${midY} ${parentPos.x},${midY} ${parentPos.x},${parentPos.y}`,
            fill: 'none', stroke: pos.color || '#30363d',
            'stroke-width': 2, opacity: 0.4,
          }));
        }
      }
    }
  }

  // ── SVG defs for HEAD glow filter ──
  const defs = svgEl('defs', {});
  const filter = svgEl('filter', { id: 'head-glow', x: '-50%', y: '-50%', width: '200%', height: '200%' });
  const feGlow = svgEl('feGaussianBlur', { stdDeviation: 4, result: 'glow' });
  filter.appendChild(feGlow);
  const feMerge = svgEl('feMerge', {});
  feMerge.appendChild(svgEl('feMergeNode', { in: 'glow' }));
  feMerge.appendChild(svgEl('feMergeNode', { in: 'SourceGraphic' }));
  filter.appendChild(feMerge);
  defs.appendChild(filter);
  svg.appendChild(defs);

  // ── Commit nodes (ring style, hover for details) ──
  for (const c of layout.commitPositions) {
    const g = svgEl('g', { transform: `translate(${c.x},${c.y})`, class: 'dag-node' });
    const isHead = c.is_head;
    const isMerge = c.isMerge;
    const nodeR = isMerge ? COMMIT_R + 1 : COMMIT_R;

    // Subtle glow behind HEAD commit
    if (isHead) {
      g.appendChild(svgEl('circle', {
        r: nodeR + 8, fill: '#f0883e', opacity: 0.15, filter: 'url(#head-glow)',
      }));
    }

    // Outer ring — bigger and brighter for HEAD
    g.appendChild(svgEl('circle', {
      r: isHead ? nodeR + 4 : nodeR + 2,
      fill: 'none',
      stroke: isHead ? '#f0883e' : '#fff',
      'stroke-width': isHead ? 2.5 : 1.5,
      opacity: isHead ? 1 : 0.7,
    }));
    // Inner filled circle (merge commits slightly larger)
    g.appendChild(svgEl('circle', { r: nodeR, fill: c.color }));

    // Branch labels: local branches first, then remote branches (dashed border)
    const allRefs = [
      ...c.branches.map(n => ({ name: n, remote: false })),
      ...((c.remote_branches || []).map(n => ({ name: n, remote: true }))),
    ];
    for (let i = 0; i < allRefs.length; i++) {
      const pillG = svgEl('g', { transform: `translate(${nodeR + 10}, ${-8 + i * 18})` });
      const { name: brName, remote } = allRefs[i];
      // Strip origin/ prefix for color lookup; remote refs use gray-ish tone
      const colorKey = brName.replace(/^(origin|remotes\/[^/]+)\//, '');
      const brColor = remote ? '#8b949e' : (layout.branchColorMap[colorKey] || '#8b949e');
      const pillW = brName.length * 6.5 + 12;
      pillG.appendChild(svgEl('rect', {
        x: -4, y: -10, width: pillW, height: 16, rx: 8, ry: 8,
        fill: brColor, opacity: remote ? 0.08 : 0.15,
        stroke: brColor, 'stroke-width': 0.5, 'stroke-opacity': 0.4,
        'stroke-dasharray': remote ? '3 2' : 'none',
      }));
      const textEl = svgEl('text', {
        x: pillW / 2 - 4, y: 1, 'text-anchor': 'middle',
        'font-size': 10, fill: brColor,
        'font-family': 'monospace', 'font-weight': '500',
        'font-style': remote ? 'italic' : 'normal',
      });
      textEl.textContent = brName;
      pillG.appendChild(textEl);
      g.appendChild(pillG);
    }

    // Hover tooltip
    g.style.cursor = 'pointer';
    g.addEventListener('mouseenter', (e) => {
      const refs = c.branches.length ? `<br><b>${c.branches.join(', ')}</b>` : '';
      const remotes = (c.remote_branches && c.remote_branches.length)
        ? `<br><span style="color:#8b949e">${c.remote_branches.join(', ')}</span>` : '';
      showTooltip(e, `<b>${c.short}</b> ${esc(c.message)}${refs}${remotes}<br><span style="opacity:0.6">${c.author} · ${c.date}</span><br><span style="color:#58a6ff;font-size:10px">点击 checkout 到此 commit</span>`);
    });
    g.addEventListener('mousemove', (e) => {
      tooltipEl.style.left = (e.clientX + 12) + 'px';
      tooltipEl.style.top = (e.clientY + 12) + 'px';
    });
    g.addEventListener('mouseleave', hideTooltip);
    g.addEventListener('click', (e) => {
      e.stopPropagation();
      hideTooltip();
      checkoutCommit(c.short);
    });

    svg.appendChild(g);
  }

  // ── Idea blocks: connector line from rail, tree branches to features ──
  const reqs = nodes.filter(n => n.type === 'requirement');
  const allFeatures = nodes.filter(n => n.type === 'task' || n.type === 'proposal_feature');

  for (const req of reqs) {
    const iPos = layout.positions[req.id];
    if (!iPos) continue;

    // Dashed line from main lane commit rail to idea
    const mainLaneX = COMMIT_X_BASE; // main/master is always lane 0
    svg.appendChild(svgEl('line', {
      x1: mainLaneX, y1: iPos.y, x2: iPos.x - IDEA_R, y2: iPos.y,
      stroke: '#da70d6', 'stroke-width': 1.5, 'stroke-dasharray': '6 3', opacity: 0.5,
    }));

    // Tree connector: L-shaped lines from idea to each feature
    // Trunk X is midway between idea right edge and feature left edge
    const myFeats = allFeatures.filter(f => f.requirement_id === req.id);
    if (myFeats.length) {
      const trunkX = layout.featX - 30; // trunk line is 30px left of feature nodes
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
    const depLaneX = layout.featX - 18;

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
  activateSideTab('details');

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

  const sourceIssueLink = task.source_issue
    ? `<a href="#" onclick="event.preventDefault()" style="color:var(--accent);font-family:var(--font-mono)">#${task.source_issue}</a>`
    : '-';
  html += `<div class="detail-section">
    <h3>Info</h3>
    <dl class="detail-meta">
      <dt>Status</dt>
      <dd><span class="status-badge" style="background:${STATUS_COLORS[task.status]}20;color:${STATUS_COLORS[task.status]}">${task.status}</span></dd>
      <dt>Source</dt><dd>${sourceIssueLink}</dd>
      <dt>Agent</dt><dd>${task.assigned_to || '-'}</dd>
      <dt>Dependencies</dt><dd>${task.depends_on.length ? task.depends_on.join(', ') : 'None'}</dd>
    </dl>
  </div>`;

  // Branch management — always visible when branch exists
  if (task.branch) {
    html += `<div class="detail-section branch-mgmt">
      <h3>Branch</h3>
      <div class="branch-name">${esc(task.branch)}</div>
      <div class="branch-actions">
        <button class="btn btn-compact" onclick="promptRenameBranch('${task.id}', '${esc(task.branch)}')">Rename</button>
        <button class="btn btn-compact" onclick="pushTaskBranch('${task.id}')">Push branch</button>
        <button class="btn btn-compact" onclick="mergeAndPush('${task.id}')">Merge → main & push</button>
      </div>
    </div>`;
  }

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
      <h3>Accept Options</h3>
      <label class="checkbox-label" style="font-size:12.5px;margin-bottom:6px">
        <input type="checkbox" id="accept-merge-${task.id}" checked>
        合并到本地主分支
      </label>
      <label class="checkbox-label" style="font-size:12.5px;margin-bottom:6px">
        <input type="checkbox" id="accept-push-${task.id}" checked>
        推送到远程
      </label>
      <label class="checkbox-label" style="font-size:12.5px;margin-bottom:10px">
        <input type="checkbox" id="accept-pr-${task.id}">
        创建 PR（不合并时使用）
      </label>
      <div class="review-actions">
        <button class="btn btn-primary" onclick="reviewTaskAccept('${task.id}')">Accept</button>
        <button class="btn btn-reject" onclick="showReject('${task.id}')">Reject</button>
      </div>
      <div id="reject-form-${task.id}" style="display:none;margin-top:10px">
        <textarea id="reject-reason-${task.id}" rows="3" placeholder="打回理由 — FR 会根据这个反馈重新实现..."></textarea>
        <button class="btn btn-reject" style="margin-top:6px" onclick="reviewTask('${task.id}', 'reject')">Confirm Reject</button>
      </div>
    </div>`;
  }

  // (push-main is now accessible from the branch management block)

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

async function reviewTaskAccept(taskId) {
  const mergeLocal = document.getElementById(`accept-merge-${taskId}`).checked;
  const push = document.getElementById(`accept-push-${taskId}`).checked;
  const createPr = document.getElementById(`accept-pr-${taskId}`).checked;

  addLogEntry('task_accepting', `Accepting ${taskId}…`);
  const res = await fetch(`/api/tasks/${taskId}/review`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action: 'accept', merge_local: mergeLocal, push, create_pr: createPr }),
  });
  const data = await res.json();

  const parts = [];
  if (data.merged) parts.push('merged');
  if (data.pushed_branch) parts.push('pushed branch');
  if (data.pushed_main) parts.push('pushed main');
  if (data.pr_created) parts.push(`PR: ${data.pr_url}`);
  if (data.merge_message && !data.merged) parts.push(`merge: ${data.merge_message}`);
  addLogEntry('task_accepted', `${taskId}: ${parts.join(', ') || 'done'}`);

  await fetchGraph();
  const node = graphData.nodes.find(n => n.id === taskId);
  if (node) onNodeClick(node);
}

async function pushTaskBranch(taskId) {
  addLogEntry('push_branch', `Pushing ${taskId} branch…`);
  try {
    const res = await fetch(`/api/tasks/${taskId}/push`, { method: 'POST' });
    const data = await res.json();
    addLogEntry('push_branch', data.pushed ? `${taskId} branch pushed` : `Push failed`);
    await fetchGraph();
  } catch (e) {
    addLogEntry('push_branch', 'Error: ' + e.message);
  }
}

async function mergeAndPush(taskId) {
  if (!confirm(`将 ${taskId} 的分支合并到 main 并推送？`)) return;
  addLogEntry('merge_push', `Merging ${taskId} to main…`);
  try {
    // Use accept flow with merge+push but no PR
    const res = await fetch(`/api/tasks/${taskId}/merge`, {
      method: 'POST',
    });
    const data = await res.json();
    if (!res.ok) {
      alert('Merge failed: ' + (data.detail || data.message || 'unknown'));
      addLogEntry('merge_push', `Failed: ${data.detail || data.message || 'unknown'}`);
      return;
    }
    const parts = [];
    if (data.merged) parts.push('merged');
    if (data.pushed) parts.push('pushed');
    addLogEntry('merge_push', `${taskId}: ${parts.join(', ') || data.message || 'done'}`);
    await fetchGraph();
    const node = graphData.nodes.find(n => n.id === taskId);
    if (node) onNodeClick(node);
  } catch (e) {
    addLogEntry('merge_push', 'Error: ' + e.message);
  }
}

async function pushMain() {
  addLogEntry('push_main', 'Pushing main to remote…');
  try {
    const res = await fetch('/api/git/push-main', { method: 'POST' });
    const data = await res.json();
    addLogEntry('push_main', data.pushed ? 'Pushed successfully' : 'Push failed');
    await fetchGraph();
  } catch (e) {
    addLogEntry('push_main', 'Error: ' + e.message);
  }
}

async function promptRenameBranch(taskId, currentName) {
  const newName = prompt('New branch name:', currentName);
  if (!newName || newName === currentName) return;
  const res = await fetch(`/api/tasks/${taskId}/review`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action: 'rename_branch', new_branch: newName }),
  });
  const data = await res.json();
  if (!res.ok) {
    alert('Rename failed: ' + (data.detail || 'unknown'));
    return;
  }
  addLogEntry('branch_renamed', data.message);
  await fetchGraph();
  const node = graphData.nodes.find(n => n.id === taskId);
  if (node) onNodeClick(node);
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

{ const _btn = document.getElementById('btn-show-agents'); if (_btn) _btn.addEventListener('click', showAgentsPanel); }

async function showAgentsPanel() {
  activateSideTab('details');
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

// ── Auto-accept toggle (status pill) ──────────────────────────

document.getElementById('pill-auto-accept').addEventListener('click', async () => {
  const res = await fetch('/api/auto-accept', { method: 'POST' });
  const data = await res.json();
  updateAutoAcceptBtn(data.auto_accept);
});

function updateAutoAcceptBtn(enabled) {
  const pill = document.getElementById('pill-auto-accept');
  const value = document.getElementById('pill-auto-accept-value');
  if (!pill || !value) return;
  pill.classList.toggle('active', !!enabled);
  value.textContent = enabled ? 'on' : 'off';
}

// ── Switch project ──────────────────────────────────────────────

async function doSwitchProject() {
  if (!confirm('Disconnect from current project and switch?')) return;
  await fetch('/api/disconnect', { method: 'POST' });
  document.getElementById('dashboard').classList.add('hidden');
  document.getElementById('setup-screen').classList.remove('hidden');
  setupBrowse('~');
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

    if (['discussion_discovered','discussion_commented','discussion_ready','discussion_status_changed',
         'discussion_restored','discussion_child_found','discussion_submitted_as_idea'
         ].includes(event) && discussionsPanelOpen) {
      fetchDiscussions();
    }

    if (event === 'draft_comment_created') {
      fetchDrafts();
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
    const trackedBranch = document.getElementById('setup-tracked-branch').value.trim() || null;
    const res = await fetch('/api/init', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project_path: path, tracked_branch: trackedBranch }),
    });

    if (!res.ok) {
      const err = await res.json();
      alert(err.detail || 'Initialization failed');
      btn.disabled = false;
      btn.textContent = 'Initialize Orchestra';
      return;
    }

    const data = await res.json();
    // Stash project path so Step 3 → reload lands on the dashboard.
    window._setupProjectPath = data.project_path;
    setupShowStep3();
  } catch (e) {
    alert('Error: ' + e.message);
    btn.disabled = false;
    btn.textContent = 'Initialize Orchestra';
  }
}

// ── Setup Step 3: Run Configuration ─────────────────────────────

async function setupShowStep3() {
  document.getElementById('setup-step2').classList.add('hidden');
  document.getElementById('setup-step3').classList.remove('hidden');
  try {
    const r = await fetch('/api/run_config/detect');
    if (r.ok) {
      const cfg = await r.json();
      document.getElementById('rc-command').value = cfg.command;
      document.getElementById('rc-ready-signal').value = cfg.ready_signal || '';
      document.getElementById('rc-base-url').value = cfg.base_url;
      document.getElementById('rc-timeout').value = cfg.startup_timeout;
      const info = document.getElementById('rc-detect-info');
      info.classList.remove('hidden');
      document.getElementById('rc-detect-source').textContent = cfg.discovered_by;
    }
  } catch (e) { /* no detection */ }
}

async function runConfigTest() {
  const body = _runConfigBody();
  const result = document.getElementById('rc-test-result');
  result.textContent = 'Testing...';
  try {
    const r = await fetch('/api/run_config/test', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
    const data = await r.json();
    if (data.ok) {
      result.innerHTML = '<span class="success">✓ Started successfully</span>';
      document.getElementById('rc-save-btn').disabled = false;
    } else {
      result.innerHTML = `<span class="error">✗ ${esc(data.error || 'Test failed')}</span>`;
    }
  } catch (e) {
    result.innerHTML = `<span class="error">✗ ${esc(e.message)}</span>`;
  }
}

async function runConfigSave() {
  const r = await fetch('/api/run_config', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(_runConfigBody()),
  });
  if (r.ok) {
    document.getElementById('setup-step3').classList.add('hidden');
    document.getElementById('setup-screen').classList.add('hidden');
    location.reload();
  } else {
    const result = document.getElementById('rc-test-result');
    let msg = 'Save failed';
    try { const data = await r.json(); msg = data.detail || msg; } catch (e) {}
    result.innerHTML = `<span class="error">✗ ${esc(msg)}</span>`;
  }
}

function _runConfigBody() {
  return {
    command: document.getElementById('rc-command').value,
    ready_signal: document.getElementById('rc-ready-signal').value || null,
    base_url: document.getElementById('rc-base-url').value,
    startup_timeout: parseInt(document.getElementById('rc-timeout').value, 10),
  };
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
  fetchTrackingStatus();
  fetchDrafts();
  fetchTrackedBranch();
  setInterval(fetchAgents, 5000);
  setInterval(fetchGraph, 10000);
  setInterval(fetchDrafts, 10000);
  setInterval(() => {
    const active = document.querySelector('.side-tab.active[data-side-tab]');
    if (!active) return;
    if (active.dataset.sideTab === 'issues') fetchIssues();
    else if (active.dataset.sideTab === 'prs') fetchPRs();
  }, 30000);
  connectSSE();
}

// ── Tracked Branch / Git ──────────────────────────────────────

let currentTrackedBranch = null;

async function fetchTrackedBranch() {
  try {
    const res = await fetch('/api/tracked-branch');
    if (!res.ok) return;
    const data = await res.json();
    currentTrackedBranch = data.branch;
    updateTrackedBranchBtn();
  } catch (e) {}
}

function updateTrackedBranchBtn() {
  const pill = document.getElementById('pill-branch');
  const value = document.getElementById('pill-branch-value');
  if (!pill || !value) return;
  value.textContent = currentTrackedBranch || '—';
  pill.classList.toggle('active', !!currentTrackedBranch);
}

async function promptTrackedBranch() {
  const current = currentTrackedBranch || 'main';
  const input = prompt('输入要跟踪的分支 (留空清除):', current);
  if (input === null) return;
  const branch = input.trim() || null;

  try {
    const res = await fetch('/api/tracked-branch', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ branch }),
    });
    const data = await res.json();
    currentTrackedBranch = branch;
    updateTrackedBranchBtn();
    if (data.message) addLogEntry('tracked_branch', data.message);
    await fetchGraph();
  } catch (e) {
    alert('设置失败: ' + e.message);
  }
}

async function gitFetch() {
  addLogEntry('git_fetch', 'Fetching from origin…');
  try {
    await fetch('/api/git/fetch', { method: 'POST' });
    addLogEntry('git_fetch', 'Fetched latest from origin');
    await fetchGraph();
  } catch (e) {}
}

async function checkoutCommit(ref) {
  try {
    const res = await fetch('/api/checkout', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ref, force: false }),
    });
    const data = await res.json();

    if (data.status === 'dirty') {
      // Show details and ask user whether to force
      let msg = `工作区有未保存的变更，切换到 ${ref} 需要放弃这些修改：\n\n`;
      if (data.files && data.files.length) {
        msg += '修改的文件:\n' + data.files.slice(0, 10).map(f => '  ' + f).join('\n');
        if (data.files.length > 10) msg += `\n  ... 还有 ${data.files.length - 10} 个文件`;
        msg += '\n\n';
      }
      if (data.unpushed_commits && data.unpushed_commits.length) {
        msg += '未推送的 commit:\n' + data.unpushed_commits.slice(0, 5).map(c => '  ' + c).join('\n');
        if (data.unpushed_commits.length > 5) msg += `\n  ... 还有 ${data.unpushed_commits.length - 5} 个`;
        msg += '\n\n';
      }
      msg += '确认放弃所有本地修改并切换？';

      if (!confirm(msg)) return;

      // Force checkout
      const res2 = await fetch('/api/checkout', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ref, force: true }),
      });
      const data2 = await res2.json();
      if (!res2.ok || data2.status !== 'ok') {
        alert('Checkout 失败: ' + (data2.detail || data2.message || 'unknown'));
        return;
      }
      addLogEntry('checkout', `[force] ${data2.message}`);
      await fetchGraph();
      return;
    }

    if (!res.ok || data.status !== 'ok') {
      alert('Checkout 失败: ' + (data.detail || data.message || 'unknown'));
      return;
    }
    addLogEntry('checkout', data.message);
    await fetchGraph();
  } catch (e) {
    alert('Checkout 失败: ' + e.message);
  }
}

// Wire up buttons
// (Legacy buttons removed — see new menu wiring at bottom of file)

// ── Panel Resizers ─────────────────────────────────────────────

(function initResizers() {
  // Horizontal resizer: between graph-panel and detail-panel
  const resizeH = document.getElementById('resize-h');
  const graphPanel = document.getElementById('graph-panel');
  const detailPanel = document.getElementById('detail-panel');

  if (resizeH && graphPanel && detailPanel) {
    let startX, startGraphW, startDetailW;

    resizeH.addEventListener('mousedown', (e) => {
      e.preventDefault();
      startX = e.clientX;
      startGraphW = graphPanel.offsetWidth;
      startDetailW = detailPanel.offsetWidth;
      document.body.classList.add('resizing');
      resizeH.classList.add('active');

      function onMove(e) {
        const dx = e.clientX - startX;
        const newGraphW = Math.max(250, startGraphW + dx);
        const newDetailW = Math.max(200, startDetailW - dx);
        graphPanel.style.flex = 'none';
        graphPanel.style.width = newGraphW + 'px';
        detailPanel.style.width = newDetailW + 'px';
        detailPanel.style.minWidth = '200px';
      }
      function onUp() {
        document.body.classList.remove('resizing');
        resizeH.classList.remove('active');
        document.removeEventListener('mousemove', onMove);
        document.removeEventListener('mouseup', onUp);
      }
      document.addEventListener('mousemove', onMove);
      document.addEventListener('mouseup', onUp);
    });
  }

  // Vertical resizer: between main and footer
  const resizeV = document.getElementById('resize-v');
  const footer = document.getElementById('event-log');
  const mainEl = document.querySelector('main');

  if (resizeV && footer && mainEl) {
    let startY, startFooterH;

    resizeV.addEventListener('mousedown', (e) => {
      e.preventDefault();
      startY = e.clientY;
      startFooterH = footer.offsetHeight;
      document.body.classList.add('resizing-v');
      resizeV.classList.add('active');

      function onMove(e) {
        const dy = startY - e.clientY;
        const newH = Math.max(60, Math.min(window.innerHeight * 0.6, startFooterH + dy));
        footer.style.height = newH + 'px';
      }
      function onUp() {
        document.body.classList.remove('resizing-v');
        resizeV.classList.remove('active');
        document.removeEventListener('mousemove', onMove);
        document.removeEventListener('mouseup', onUp);
      }
      document.addEventListener('mousemove', onMove);
      document.addEventListener('mouseup', onUp);
    });
  }
})();

// ── Issues Tab ─────────────────────────────────────────────────

let issuesState = 'open';
let issuesCache = [];

async function fetchIssues(state) {
  if (state) issuesState = state;
  const list = document.getElementById('issues-list');
  if (!list) return;

  // Show cached data immediately (no flash), only show loading on first load
  if (!issuesCache.length) {
    list.innerHTML = '<div class="issues-loading">Loading issues...</div>';
  }

  try {
    const res = await fetch(`/api/issues?state=${issuesState}`);
    if (!res.ok) throw new Error('Failed to fetch');
    const fresh = await res.json();
    // Only re-render if data actually changed
    if (JSON.stringify(fresh) !== JSON.stringify(issuesCache)) {
      issuesCache = fresh;
      renderIssuesList(issuesCache);
    }
  } catch (e) {
    // On error, keep showing cache if available
    if (!issuesCache.length) {
      list.innerHTML = `<div class="issues-empty">Could not load issues: ${esc(e.message)}</div>`;
    }
  }
}

function renderIssuesList(issues) {
  const list = document.getElementById('issues-list');
  if (!list) return;

  if (!issues.length) {
    list.innerHTML = '<div class="issues-empty">No issues found</div>';
    return;
  }

  let html = '';
  for (const issue of issues) {
    const labels = (issue.labels || []).map(l => {
      const cls = ['discuss','idea','feat','bug','rfc','orchestra-ready'].includes(l) ? ` l-${l}` : '';
      return `<span class="issue-label${cls}">${esc(l)}</span>`;
    }).join('');

    const timeAgo = formatTimeAgo(issue.updated_at || issue.created_at);
    const comments = issue.comment_count || 0;
    const url = issue.url || '#';

    html += `<div class="issue-row">
      <span class="issue-number">#${issue.number}</span>
      <div class="issue-main">
        <div class="issue-title"><a href="${esc(url)}" target="_blank" rel="noopener">${esc(issue.title)}</a></div>
        <div class="issue-meta">
          @${esc(issue.author)} · ${timeAgo}
          ${labels ? '<span class="issue-labels">' + labels + '</span>' : ''}
        </div>
      </div>
      <div class="issue-actions">
        <span class="issue-comments">${comments > 0 ? comments + ' 💬' : ''}</span>
        <button class="btn btn-compact issue-make-idea"
                data-issue="${issue.number}">
          + Idea
        </button>
      </div>
    </div>`;
  }
  list.innerHTML = html;

  list.querySelectorAll('.issue-make-idea').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      makeIdeaFromIssue(parseInt(btn.dataset.issue, 10));
    });
  });
}

async function makeIdeaFromIssue(issueNumber) {
  const instruction = prompt(
    `从 issue #${issueNumber} 创建 Idea，可以附加说明（可选）：`,
    ''
  );
  if (instruction === null) return;  // cancelled

  addLogEntry('issue_to_idea', `Submitting #${issueNumber} to Head Leader…`);

  try {
    const res = await fetch(`/api/issues/${issueNumber}/idea`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ instruction: instruction.trim() }),
    });
    const data = await res.json();
    if (!res.ok) {
      alert('Failed: ' + (data.detail || 'unknown'));
      return;
    }
    addLogEntry('issue_to_idea', `#${issueNumber} → proposal ${data.proposal_id}`);
    await fetchGraph();
  } catch (e) {
    alert('Failed: ' + e.message);
  }
}

// ── PRs tab ──────────────────────────────────────────────────

let prsState = 'open';
let prsCache = [];

async function fetchPRs(state) {
  if (state) prsState = state;
  const list = document.getElementById('prs-list');
  if (!list) return;

  if (!prsCache.length) {
    list.innerHTML = '<div class="issues-loading">Loading PRs...</div>';
  }
  try {
    const res = await fetch(`/api/prs?state=${prsState}`);
    if (!res.ok) throw new Error('Failed to fetch');
    const fresh = await res.json();
    if (JSON.stringify(fresh) !== JSON.stringify(prsCache)) {
      prsCache = fresh;
      renderPRsList(prsCache);
    }
  } catch (e) {
    if (!prsCache.length) {
      list.innerHTML = `<div class="issues-empty">Could not load PRs: ${esc(e.message)}</div>`;
    }
  }
}

function renderPRsList(prs) {
  const list = document.getElementById('prs-list');
  if (!list) return;
  if (!prs.length) {
    list.innerHTML = '<div class="issues-empty">No PRs found</div>';
    return;
  }

  let html = '';
  for (const pr of prs) {
    const labels = (pr.labels || []).map(l => {
      const cls = ['discuss','idea','feat','bug','rfc','orchestra-ready'].includes(l) ? ` l-${l}` : '';
      return `<span class="issue-label${cls}">${esc(l)}</span>`;
    }).join('');
    const timeAgo = formatTimeAgo(pr.updated_at || pr.created_at);
    const comments = pr.comment_count || 0;
    const url = pr.url || '#';
    const state = (pr.state || 'open').toLowerCase();
    const stateClass = state === 'merged' ? 'merged' : state === 'closed' ? 'closed' : 'open';

    html += `<div class="issue-row">
      <span class="issue-number">#${pr.number}</span>
      <div class="issue-main">
        <div class="issue-title">
          <span class="issue-state-pill ${stateClass}">${state}</span>
          <a href="${esc(url)}" target="_blank" rel="noopener">${esc(pr.title)}</a>
        </div>
        <div class="issue-meta">
          @${esc(pr.author)} · ${timeAgo} · <span style="color:var(--text-dim)">${esc(pr.head || '')} → ${esc(pr.base || '')}</span>
          ${labels ? '<span class="issue-labels">' + labels + '</span>' : ''}
        </div>
      </div>
      <span class="issue-comments">${comments > 0 ? comments + ' comment' + (comments > 1 ? 's' : '') : ''}</span>
    </div>`;
  }
  list.innerHTML = html;
}

function formatTimeAgo(dateStr) {
  if (!dateStr) return '';
  try {
    const d = new Date(dateStr);
    const now = new Date();
    const diffMs = now - d;
    const diffMin = Math.floor(diffMs / 60000);
    if (diffMin < 1) return 'just now';
    if (diffMin < 60) return `${diffMin}m ago`;
    const diffH = Math.floor(diffMin / 60);
    if (diffH < 24) return `${diffH}h ago`;
    const diffD = Math.floor(diffH / 24);
    if (diffD < 30) return `${diffD}d ago`;
    return d.toLocaleDateString();
  } catch { return dateStr; }
}

// Issues filter buttons
document.querySelectorAll('.filter[data-target]').forEach(btn => {
  btn.addEventListener('click', () => {
    const target = btn.dataset.target;
    document.querySelectorAll(`.filter[data-target="${target}"]`).forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    if (target === 'issues') fetchIssues(btn.dataset.state);
    else if (target === 'prs') fetchPRs(btn.dataset.state);
  });
});

// ── Draft Comments ─────────────────────────────────────────────

let draftsCache = [];

async function fetchDrafts() {
  try {
    const res = await fetch('/api/drafts?status=pending');
    if (!res.ok) return;
    const fresh = await res.json();
    if (JSON.stringify(fresh) !== JSON.stringify(draftsCache)) {
      draftsCache = fresh;
      renderDrafts(draftsCache);
    }
    // Update badge
    const badge = document.getElementById('drafts-count');
    if (badge) {
      if (draftsCache.length > 0) {
        badge.textContent = draftsCache.length;
        badge.classList.remove('hidden');
      } else {
        badge.classList.add('hidden');
      }
    }
  } catch (e) {}
}

function renderDrafts(drafts) {
  const list = document.getElementById('drafts-list');
  if (!list) return;

  if (!drafts.length) {
    list.innerHTML = '<div class="issues-empty">没有待审核的评论草稿</div>';
    return;
  }

  let html = '';
  for (const d of drafts) {
    html += `<div class="draft-card" id="draft-${d.id}">
      <div class="draft-card-header">
        <span class="draft-source ${esc(d.source)}">${d.source === 'head_leader' ? 'Head Leader' : 'Analyst'}</span>
        <span style="color:var(--accent);font-family:monospace">#${d.target_issue}</span>
        <span style="color:var(--text-dim)">→ Tree #${d.root_issue}</span>
      </div>
      <div class="draft-body" id="draft-body-${d.id}">${marked.parse(d.body)}</div>
      <div class="draft-actions">
        <button class="btn btn-primary" onclick="reviewDraft(${d.id}, 'approve')">发布到 GitHub</button>
        <button class="btn" onclick="editDraft(${d.id})">编辑</button>
        <button class="btn" onclick="toggleDraftChat(${d.id})">讨论</button>
        <button class="btn btn-reject" onclick="reviewDraft(${d.id}, 'reject')">丢弃</button>
      </div>
      <div class="draft-chat hidden" id="draft-chat-${d.id}">
        <div class="draft-chat-messages" id="draft-chat-msgs-${d.id}"></div>
        <div class="draft-chat-input-row">
          <textarea class="draft-chat-input" id="draft-chat-input-${d.id}"
                    placeholder="和 agent 讨论... (Enter 发送, Shift+Enter 换行)"
                    rows="1"></textarea>
          <button class="btn" onclick="sendDraftChat(${d.id})">发送</button>
          <button class="btn btn-rewrite" onclick="rewriteDraft(${d.id})">重写草稿</button>
        </div>
      </div>
    </div>`;
  }
  list.innerHTML = html;
}

async function reviewDraft(draftId, action) {
  const body = {};
  body.action = action;
  if (action === 'approve') {
    // Check if user edited the draft
    const editArea = document.getElementById(`draft-edit-${draftId}`);
    if (editArea) {
      // Save edit first, then approve
      await fetch(`/api/drafts/${draftId}/review`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'edit', body: editArea.value }),
      });
    }
  }
  await fetch(`/api/drafts/${draftId}/review`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  addLogEntry('draft_' + action, `Draft #${draftId} ${action === 'approve' ? '已发布' : '已丢弃'}`);
  fetchDrafts();
}

function editDraft(draftId) {
  const draft = draftsCache.find(d => d.id === draftId);
  if (!draft) return;
  const card = document.getElementById(`draft-${draftId}`);
  if (!card) return;
  const bodyDiv = document.getElementById(`draft-body-${draftId}`);
  if (!bodyDiv) return;

  // Replace body display with editable textarea (use .value, not innerHTML)
  const textarea = document.createElement('textarea');
  textarea.className = 'draft-edit-area';
  textarea.id = `draft-edit-${draftId}`;
  textarea.value = draft.body;  // raw text, no HTML escaping
  bodyDiv.replaceWith(textarea);

  // Replace action buttons
  const actions = card.querySelector('.draft-actions');
  actions.innerHTML = '';

  const saveBtn = document.createElement('button');
  saveBtn.className = 'btn btn-primary';
  saveBtn.textContent = '保存';
  saveBtn.addEventListener('click', () => saveDraftEdit(draftId));

  const cancelBtn = document.createElement('button');
  cancelBtn.className = 'btn';
  cancelBtn.textContent = '取消';
  cancelBtn.addEventListener('click', () => cancelDraftEdit(draftId));

  actions.appendChild(saveBtn);
  actions.appendChild(cancelBtn);
}

function cancelDraftEdit(draftId) {
  renderDrafts(draftsCache);
}

// ── Draft Chat ─────────────────────────────────────────────────

async function toggleDraftChat(draftId) {
  const panel = document.getElementById(`draft-chat-${draftId}`);
  if (!panel) return;
  const isHidden = panel.classList.contains('hidden');
  panel.classList.toggle('hidden');

  if (isHidden) {
    await loadDraftChat(draftId);
    const input = document.getElementById(`draft-chat-input-${draftId}`);
    if (input) {
      input.focus();
      // Auto-grow textarea
      const autoGrow = () => {
        input.style.height = 'auto';
        input.style.height = Math.min(input.scrollHeight, 120) + 'px';
      };
      input.addEventListener('input', autoGrow);
      input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
          e.preventDefault();
          sendDraftChat(draftId);
          // Reset height after send
          input.style.height = 'auto';
        }
      });
    }
  }
}

async function loadDraftChat(draftId) {
  const container = document.getElementById(`draft-chat-msgs-${draftId}`);
  if (!container) return;
  try {
    const res = await fetch(`/api/drafts/${draftId}/chat`);
    if (!res.ok) return;
    const messages = await res.json();
    renderDraftChatMessages(container, messages, draftId);
  } catch (e) {}
}

function renderDraftChatMessages(container, messages, draftId) {
  if (!messages.length) {
    container.innerHTML = '<div class="draft-chat-empty">和 agent 讨论如何修改这条草稿</div>';
    return;
  }
  container.innerHTML = messages.map((m, i) => {
    const applyBtn = m.role === 'assistant'
      ? `<button class="btn draft-chat-apply" data-msg-idx="${i}">应用为草稿</button>`
      : '';
    return `<div class="draft-chat-msg ${esc(m.role)}">
      <span class="draft-chat-role">${m.role === 'user' ? '你' : 'Agent'}</span>
      <div class="draft-chat-content">${marked.parse(m.content)}</div>
      ${applyBtn}
    </div>`;
  }).join('');

  // Bind apply buttons
  container.querySelectorAll('.draft-chat-apply').forEach(btn => {
    btn.addEventListener('click', () => {
      const idx = parseInt(btn.dataset.msgIdx, 10);
      const msg = messages[idx];
      if (msg) applyMsgAsDraft(draftId, msg.content);
    });
  });

  container.scrollTop = container.scrollHeight;
}

async function rewriteDraft(draftId) {
  const input = document.getElementById(`draft-chat-input-${draftId}`);
  const instruction = input ? input.value.trim() : '';
  if (input) input.value = '';

  const container = document.getElementById(`draft-chat-msgs-${draftId}`);
  // Show rewrite indicator
  if (container) {
    container.innerHTML += `<div class="draft-chat-msg user">
      <span class="draft-chat-role">你</span>
      <div class="draft-chat-content">${esc(instruction || '重写草稿')}</div>
    </div>
    <div class="draft-chat-msg assistant draft-chat-thinking" id="draft-rewrite-pending-${draftId}">
      <span class="draft-chat-role">Agent</span>
      <div class="draft-chat-content">正在重写草稿...</div>
    </div>`;
    container.scrollTop = container.scrollHeight;
  }

  try {
    const res = await fetch(`/api/drafts/${draftId}/rewrite`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: instruction || '请根据之前的讨论重写这条草稿' }),
    });
    const data = await res.json();

    // Remove thinking indicator
    const pending = document.getElementById(`draft-rewrite-pending-${draftId}`);
    if (pending) {
      pending.outerHTML = `<div class="draft-chat-msg assistant">
        <span class="draft-chat-role">Agent</span>
        <div class="draft-chat-content"><em>草稿已重写</em></div>
      </div>`;
    }

    // Update draft body display
    if (data.body) {
      const draft = draftsCache.find(d => d.id === draftId);
      if (draft) draft.body = data.body;
      const bodyEl = document.getElementById(`draft-body-${draftId}`);
      if (bodyEl) bodyEl.innerHTML = marked.parse(data.body);
      addLogEntry('draft_rewritten', `Draft #${draftId} 已重写`);
    }
  } catch (e) {
    const pending = document.getElementById(`draft-rewrite-pending-${draftId}`);
    if (pending) pending.querySelector('.draft-chat-content').textContent = '重写失败: ' + e.message;
  }

  if (container) container.scrollTop = container.scrollHeight;
}

async function applyMsgAsDraft(draftId, content) {
  await fetch(`/api/drafts/${draftId}/review`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action: 'edit', body: content }),
  });
  const draft = draftsCache.find(d => d.id === draftId);
  if (draft) draft.body = content;
  const bodyEl = document.getElementById(`draft-body-${draftId}`);
  if (bodyEl) bodyEl.innerHTML = marked.parse(content);
  addLogEntry('draft_applied', `Draft #${draftId} 已更新为 agent 的回复`);
}

async function sendDraftChat(draftId) {
  const input = document.getElementById(`draft-chat-input-${draftId}`);
  if (!input || !input.value.trim()) return;
  const message = input.value.trim();
  input.value = '';
  input.disabled = true;

  const container = document.getElementById(`draft-chat-msgs-${draftId}`);
  // Append user message immediately
  container.innerHTML += `<div class="draft-chat-msg user">
    <span class="draft-chat-role">你</span>
    <div class="draft-chat-content">${esc(message)}</div>
  </div>
  <div class="draft-chat-msg assistant draft-chat-thinking" id="draft-chat-pending-${draftId}">
    <span class="draft-chat-role">Agent</span>
    <div class="draft-chat-content">思考中...</div>
  </div>`;
  container.scrollTop = container.scrollHeight;

  try {
    const res = await fetch(`/api/drafts/${draftId}/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message }),
    });
    const data = await res.json();

    const pending = document.getElementById(`draft-chat-pending-${draftId}`);
    const replyContent = data.reply || '';
    if (pending) {
      const msgDiv = document.createElement('div');
      msgDiv.className = 'draft-chat-msg assistant';
      msgDiv.innerHTML = `
        <span class="draft-chat-role">Agent</span>
        <div class="draft-chat-content">${marked.parse(replyContent)}</div>
        <button class="btn draft-chat-apply">应用为草稿</button>`;
      msgDiv.querySelector('.draft-chat-apply').addEventListener('click', () => {
        applyMsgAsDraft(draftId, replyContent);
      });
      pending.replaceWith(msgDiv);
    }
  } catch (e) {
    const pending = document.getElementById(`draft-chat-pending-${draftId}`);
    if (pending) pending.querySelector('.draft-chat-content').textContent = '请求失败: ' + e.message;
  }

  input.disabled = false;
  input.focus();
  container.scrollTop = container.scrollHeight;
}

async function saveDraftEdit(draftId) {
  const textarea = document.getElementById(`draft-edit-${draftId}`);
  if (!textarea) return;
  const newBody = textarea.value;
  await fetch(`/api/drafts/${draftId}/review`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action: 'edit', body: newBody }),
  });
  // Update cache so re-render shows new content
  const draft = draftsCache.find(d => d.id === draftId);
  if (draft) draft.body = newBody;
  renderDrafts(draftsCache);
}

// ── Discussion Tracking ────────────────────────────────────────

let watchActive = false;
let discussionsPanelOpen = false;

async function toggleWatch() {
  try {
    if (!watchActive) {
      const res = await fetch('/api/tracking/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ labels: watchLabels, focus_issues: focusIssues, poll_interval: 120, auto_submit: false }),
      });
      if (res.ok) watchActive = true;
    } else {
      const res = await fetch('/api/tracking/stop', { method: 'POST' });
      if (res.ok) watchActive = false;
    }
  } catch (e) {
    console.error('toggleWatch error:', e);
  }
  updateWatchBtn();
}

function updateWatchBtn() {
  const pill = document.getElementById('pill-watch');
  const value = document.getElementById('pill-watch-value');
  if (!pill || !value) return;
  pill.classList.toggle('active', watchActive);
  value.textContent = watchActive ? 'on' : 'off';
  return;
  btn.style.borderColor = watchActive ? '#238636' : '';
  btn.style.color = watchActive ? '#3fb950' : '';
}

async function fetchTrackingStatus() {
  try {
    const res = await fetch('/api/tracking/status');
    if (res.ok) {
      const data = await res.json();
      watchActive = data.active;
      if (data.labels) watchLabels = data.labels;
      if (data.focus_issues) focusIssues = data.focus_issues;
      updateWatchBtn();
      renderTagPills();
      renderFocusPills();
    }
  } catch (e) {
    // tracking endpoint may not exist yet
  }
  // Also fetch saved labels/focus if tracker not active
  try {
    const [labelsRes, focusRes] = await Promise.all([
      fetch('/api/tracking/labels'),
      fetch('/api/tracking/focus'),
    ]);
    if (labelsRes.ok) {
      const data = await labelsRes.json();
      if (data.labels) watchLabels = data.labels;
      renderTagPills();
    }
    if (focusRes.ok) {
      const data = await focusRes.json();
      if (data.issues) focusIssues = data.issues;
      renderFocusPills();
    }
  } catch (e) {}
}

// ── Tag Editor ─────────────────────────────────────────────────

let watchLabels = ['discuss'];
let tagEditorOpen = false;

function renderTagPills() {
  const container = document.getElementById('tag-pills');
  if (!container) return;
  container.innerHTML = watchLabels.map(label =>
    `<span class="tag-pill">${esc(label)}<button class="tag-pill-remove" data-label="${esc(label)}">&times;</button></span>`
  ).join('');
  container.querySelectorAll('.tag-pill-remove[data-label]').forEach(btn => {
    btn.addEventListener('click', () => removeWatchTag(btn.dataset.label));
  });
}

async function addWatchTag() {
  const input = document.getElementById('tag-input');
  const tag = input.value.trim().toLowerCase();
  if (!tag || watchLabels.includes(tag)) { input.value = ''; return; }

  watchLabels.push(tag);
  input.value = '';
  renderTagPills();
  await saveWatchLabels();
}

async function removeWatchTag(tag) {
  watchLabels = watchLabels.filter(l => l !== tag);
  if (!watchLabels.length) watchLabels = ['discuss']; // always keep at least one
  renderTagPills();
  await saveWatchLabels();
}

async function saveWatchLabels() {
  try {
    await fetch('/api/tracking/labels', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ labels: watchLabels }),
    });
    // Immediately refresh issues list with new labels
    fetchIssues();
  } catch (e) {
    console.error('saveWatchLabels error:', e);
  }
}

// ── Focus Issues Editor ─────────────────────────────────────────

let focusIssues = [];

function renderFocusPills() {
  const container = document.getElementById('focus-pills');
  if (!container) return;
  if (!focusIssues.length) {
    container.innerHTML = '<span style="font-size:11px;color:var(--text-dim)">No pinned issues</span>';
    return;
  }
  container.innerHTML = focusIssues.map(num =>
    `<span class="tag-pill focus">#${num}<button class="tag-pill-remove" data-issue="${num}">&times;</button></span>`
  ).join('');
  container.querySelectorAll('.tag-pill-remove[data-issue]').forEach(btn => {
    btn.addEventListener('click', () => removeFocusIssue(parseInt(btn.dataset.issue, 10)));
  });
}

let _analyzingIssues = new Set();

async function addFocusIssue() {
  const input = document.getElementById('focus-input');
  const num = parseInt(input.value, 10);
  if (!num || num < 1) { input.value = ''; return; }
  if (focusIssues.includes(num) || _analyzingIssues.has(num)) { input.value = ''; return; }

  focusIssues.push(num);
  focusIssues.sort((a, b) => a - b);
  input.value = '';
  renderFocusPills();
  await saveFocusIssues();

  // Immediately trigger analysis (with dedup guard)
  _analyzingIssues.add(num);
  try {
    await fetch(`/api/discussions/${num}/analyze`, { method: 'POST' });
    addLogEntry('focus_analyze', `Started analyzing issue #${num}`);
  } catch (e) {}
  // Keep in set until agent finishes (cleared by SSE event or timeout)
  setTimeout(() => _analyzingIssues.delete(num), 300000);
}

async function removeFocusIssue(num) {
  focusIssues = focusIssues.filter(n => n !== num);
  renderFocusPills();
  await saveFocusIssues();
}

async function saveFocusIssues() {
  try {
    await fetch('/api/tracking/focus', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ issues: focusIssues }),
    });
  } catch (e) {
    console.error('saveFocusIssues error:', e);
  }
}

document.getElementById('focus-input').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') { e.preventDefault(); addFocusIssue(); }
});

function toggleTagEditor() {
  tagEditorOpen = !tagEditorOpen;
  const popover = document.getElementById('watch-editor');
  if (popover) popover.classList.toggle('hidden', !tagEditorOpen);
}

function closeTagEditor() {
  tagEditorOpen = false;
  const popover = document.getElementById('watch-editor');
  if (popover) popover.classList.add('hidden');
}

// Close tag editor on outside click
document.addEventListener('click', (e) => {
  if (tagEditorOpen &&
      !e.target.closest('#watch-editor') &&
      !e.target.closest('#menu-watch-labels') &&
      !e.target.closest('#pill-watch')) {
    closeTagEditor();
  }
});

// (btn-watch-tags removed — watch editor opens from overflow menu now)

// Enter key in tag input
document.getElementById('tag-input').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') { e.preventDefault(); addWatchTag(); }
});

async function fetchDiscussions() {
  const listEl = document.getElementById('discussions-list');
  discussionsPanelOpen = true;
  if (!listEl) return;

  try {
    const res = await fetch('/api/discussions');
    if (!res.ok) throw new Error('Failed to fetch discussions');
    const discussions = await res.json();
    renderDiscussions(discussions);
  } catch (e) {
    listEl.innerHTML = `<div class="disc-empty">
      <p><b>Could not load discussions</b></p>
      <p>${esc(e.message)}</p>
    </div>`;
  }
}

async function fetchDiscussionDetail(rootIssue) {
  const titleEl = document.getElementById('detail-title');
  const contentEl = document.getElementById('detail-content');
  titleEl.textContent = `Discussion #${rootIssue}`;
  activateSideTab('details');

  try {
    const res = await fetch(`/api/discussions/${encodeURIComponent(rootIssue)}`);
    if (!res.ok) throw new Error('Failed to fetch discussion detail');
    const tree = await res.json();
    renderDiscussionDetail(tree);
  } catch (e) {
    contentEl.innerHTML = `<div class="disc-empty">
      <p><b>Could not load discussion</b></p>
      <p>${esc(e.message)}</p>
    </div>`;
  }
}

async function submitDiscussion(rootIssue) {
  try {
    const res = await fetch(`/api/discussions/${encodeURIComponent(rootIssue)}/submit`, {
      method: 'POST',
    });
    if (res.ok) {
      addLogEntry('discussion_submitted', `Discussion #${rootIssue} submitted for implementation`);
      fetchDiscussionDetail(rootIssue);
    }
  } catch (e) {
    console.error('submitDiscussion error:', e);
  }
}

async function submitDiscussionAsIdea(rootIssue) {
  const textarea = document.getElementById(`disc-idea-instruction-${rootIssue}`);
  const instruction = textarea ? textarea.value.trim() : '';

  try {
    const res = await fetch(`/api/discussions/${rootIssue}/idea`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ instruction }),
    });
    const data = await res.json();
    if (res.ok) {
      addLogEntry('idea_from_discussion', `Discussion #${rootIssue} → Idea (proposal: ${data.proposal_id})`);
      fetchDiscussionDetail(rootIssue);
      fetchGraph();
    } else {
      alert('创建失败: ' + (data.detail || 'unknown'));
    }
  } catch (e) {
    alert('创建失败: ' + e.message);
  }
}

function renderDiscussions(discussions) {
  const contentEl = document.getElementById('discussions-list');
  if (!contentEl) return;

  if (!discussions || !discussions.length) {
    contentEl.innerHTML = `<div class="disc-empty">
      <p><b>No tracked discussions</b></p>
      <p>Enable Watch to start tracking issues with the "discuss" label.</p>
    </div>`;
    return;
  }

  let html = '<div class="disc-panel">';
  for (const disc of discussions) {
    const status = disc.status || 'watching';
    const issueCount = disc.issue_count || disc.issues?.length || 0;
    const rootNum = disc.root_issue || disc.id || '';
    const title = disc.title || `Discussion #${rootNum}`;

    html += `<div class="disc-tree-card">
      <div class="disc-tree-header" onclick="fetchDiscussionDetail('${esc(String(rootNum))}')">
        <span class="disc-issue-num">#${esc(String(rootNum))}</span>
        <span class="disc-title">${esc(title)}</span>
        <span class="disc-meta">${issueCount} issue${issueCount !== 1 ? 's' : ''}</span>
        <span class="disc-status-badge ${esc(status)}">${esc(status)}</span>
      </div>`;

    // Show collapsed issue list preview
    if (disc.issues && disc.issues.length) {
      html += '<div class="disc-issue-list">';
      for (const issue of disc.issues.slice(0, 5)) {
        const isRoot = issue.is_root || issue.number === rootNum;
        html += `<div class="disc-issue-item${isRoot ? ' root' : ''}">
          <span class="disc-issue-num">#${issue.number || ''}</span>
          <span class="disc-issue-title">${esc(issue.title || '')}</span>
          <span class="disc-comment-count">${issue.comment_count || 0} comments</span>
        </div>`;
      }
      if (disc.issues.length > 5) {
        html += `<div style="font-size:11px;color:var(--text-dim);padding:4px 0">...and ${disc.issues.length - 5} more</div>`;
      }
      html += '</div>';
    }

    // Quick action: create idea directly from list
    if (status !== 'submitted') {
      html += `<div style="padding:4px 0">
        <button class="btn btn-primary" style="font-size:11px;padding:3px 10px"
                onclick="event.stopPropagation(); submitDiscussionAsIdea(${rootNum})">
          创建 Idea
        </button>
      </div>`;
    }

    html += '</div>';
  }
  html += '</div>';
  contentEl.innerHTML = html;
}

function renderDiscussionDetail(tree) {
  const contentEl = document.getElementById('detail-content');
  const status = tree.status || 'watching';
  let html = '';

  // Analysis summary
  if (tree.analysis) {
    const maturity = tree.analysis.maturity || 0;
    const maturityPct = Math.round(maturity * 100);
    const maturityColor = maturity > 0.7 ? 'var(--c-implemented)' : maturity > 0.4 ? 'var(--c-in_progress)' : 'var(--c-rejected)';
    html += `<div class="disc-analysis">
      <h4>Analyst Summary</h4>
      <div class="markdown-body" style="border:none;padding:0;background:transparent">${marked.parse(tree.analysis.summary || 'No summary yet.')}</div>
      <div class="disc-maturity">
        <span>Maturity</span>
        <div class="disc-maturity-bar">
          <div class="disc-maturity-fill" style="width:${maturityPct}%;background:${maturityColor}"></div>
        </div>
        <span>${maturityPct}%</span>
      </div>
    </div>`;
  }

  // Status badge
  html += `<div class="detail-section">
    <h3>Status</h3>
    <span class="disc-status-badge ${esc(status)}">${esc(status)}</span>
  </div>`;

  // Issue tree
  html += '<div class="detail-section disc-tree-detail"><h3>Issue Tree</h3>';
  if (tree.issues && tree.issues.length) {
    html += renderIssueTree(tree.issues, tree.root_issue);
  } else {
    html += '<p style="color:var(--text-dim);font-size:12px">No issues found</p>';
  }
  html += '</div>';

  // Recent comments
  if (tree.recent_comments && tree.recent_comments.length) {
    html += '<div class="detail-section"><h3>Recent Comments</h3>';
    for (const comment of tree.recent_comments) {
      html += `<div class="disc-comment">
        <div class="disc-comment-author">
          ${esc(comment.author || 'unknown')}
          <span class="disc-comment-time">${comment.created_at || ''}</span>
        </div>
        <div>${marked.parse(comment.body || '')}</div>
      </div>`;
    }
    html += '</div>';
  }

  // Action buttons
  if (status !== 'submitted') {
    const rootIssue = tree.root_issue || '';
    html += `<div class="detail-section disc-idea-section">
      <h3>创建 Idea</h3>
      <p style="font-size:12px;color:var(--text-dim);margin-bottom:8px">
        将此讨论树的所有 issue 内容、评论和分析汇总后提交给 Head Leader 进行需求分解
      </p>
      <textarea id="disc-idea-instruction-${rootIssue}" class="disc-idea-input"
                placeholder="附加说明（可选）：例如优先实现哪个部分、需要注意什么..." rows="2"></textarea>
      <div style="display:flex;gap:8px;margin-top:8px">
        <button class="btn btn-primary" onclick="submitDiscussionAsIdea(${rootIssue})">
          创建 Idea (${tree.issues ? tree.issues.length : 0} 个 issue)
        </button>`;
    if (status === 'ready') {
      html += `<button class="btn" onclick="submitDiscussion('${esc(String(rootIssue))}')">
          仅提交分析摘要
        </button>`;
    }
    html += `</div></div>`;
  }

  // Back link
  html += `<div style="margin-top:16px">
    <button class="btn" onclick="fetchDiscussions()" style="font-size:11px">Back to all discussions</button>
  </div>`;

  contentEl.innerHTML = html;
}

function renderIssueTree(issues, rootIssue) {
  // Build a parent->children map
  const byNumber = {};
  const children = {};
  for (const issue of issues) {
    byNumber[issue.number] = issue;
    const parent = issue.parent || null;
    if (!children[parent]) children[parent] = [];
    children[parent].push(issue);
  }

  function renderNode(issue, depth) {
    const num = issue.number || '';
    const isRoot = issue.is_root || num === rootIssue;
    const indent = depth * 20;
    let html = `<div class="disc-issue-node" style="padding-left:${indent}px">
      <div class="disc-tree-line">
        <div class="disc-dot" style="background:${isRoot ? 'var(--c-requirement)' : 'var(--accent)'}"></div>
        <div class="disc-connector"></div>
      </div>
      <div style="flex:1">
        <div>
          <span style="font-family:monospace;color:var(--accent)">#${esc(String(num))}</span>
          <span style="margin-left:6px">${esc(issue.title || '')}</span>
          <span style="color:var(--text-dim);font-size:11px;margin-left:6px">${issue.comment_count || 0} comments</span>
        </div>`;
    if (issue.snapshot) {
      html += `<div class="disc-snapshot">${esc(issue.snapshot)}</div>`;
    }
    html += `</div></div>`;

    // Render children
    const kids = children[num] || [];
    if (kids.length) {
      html += `<div class="disc-children">`;
      for (const child of kids) {
        html += renderNode(child, depth + 1);
      }
      html += '</div>';
    }
    return html;
  }

  // Find roots (issues with no parent, or whose parent is not in the set)
  const roots = issues.filter(i => !i.parent || !byNumber[i.parent]);
  let html = '';
  for (const root of roots) {
    html += renderNode(root, 0);
  }
  return html || '<p style="color:var(--text-dim);font-size:12px">Empty tree</p>';
}

// ═══════════════════════════════════════════════════════════
// New UI wiring: side tabs, overflow menu, pills, list views
// ═══════════════════════════════════════════════════════════

// ── Side Tabs (right panel) ─────────────────────────────────
const SIDE_TAB_HANDLERS = {
  drafts: () => fetchDrafts(),
  discussions: () => fetchDiscussions(),
  issues: () => { if (!issuesCache.length) fetchIssues(); },
  prs: () => { if (!prsCache.length) fetchPRs(); },
};

function activateSideTab(tabName) {
  document.querySelectorAll('.side-tab[data-side-tab]').forEach(t => {
    t.classList.toggle('active', t.dataset.sideTab === tabName);
  });
  document.querySelectorAll('.side-tab-pane').forEach(p => {
    p.classList.toggle('active', p.dataset.pane === tabName);
  });
  const handler = SIDE_TAB_HANDLERS[tabName];
  if (handler) handler();
}

document.querySelectorAll('.side-tab[data-side-tab]').forEach(tab => {
  tab.addEventListener('click', () => activateSideTab(tab.dataset.sideTab));
});

// Global accessor: when something needs to switch user to a tab
window.openSideTab = activateSideTab;

// ── Status pill: Watch toggle on click ─────────────────────
document.getElementById('pill-watch').addEventListener('click', toggleWatch);

// ── Status pill: Branch (click opens prompt) ───────────────
document.getElementById('pill-branch').addEventListener('click', promptTrackedBranch);

// ── Overflow menu ──────────────────────────────────────────
const menuBtn = document.getElementById('btn-menu');
const overflowMenu = document.getElementById('overflow-menu');
function toggleOverflowMenu(force) {
  const isHidden = overflowMenu.classList.contains('hidden');
  const show = force === true || (force === undefined && isHidden);
  overflowMenu.classList.toggle('hidden', !show);
}
menuBtn.addEventListener('click', (e) => { e.stopPropagation(); toggleOverflowMenu(); });
document.addEventListener('click', (e) => {
  if (!e.target.closest('#overflow-menu') && !e.target.closest('#btn-menu')) {
    overflowMenu.classList.add('hidden');
  }
});

// Menu items
document.getElementById('menu-fetch').addEventListener('click', () => {
  overflowMenu.classList.add('hidden');
  gitFetch();
});
document.getElementById('menu-tracked-branch').addEventListener('click', () => {
  overflowMenu.classList.add('hidden');
  promptTrackedBranch();
});
document.getElementById('menu-watch-labels').addEventListener('click', (e) => {
  overflowMenu.classList.add('hidden');
  e.stopPropagation();
  toggleTagEditor();
});
document.getElementById('menu-switch').addEventListener('click', () => {
  overflowMenu.classList.add('hidden');
  doSwitchProject();
});

// ── Close button (detail panel header) ──────────────────────
{
  const closeBtn = document.getElementById('btn-close-detail');
  if (closeBtn) closeBtn.addEventListener('click', () => {
    const content = document.getElementById('detail-content');
    if (content) content.innerHTML = '<div class="empty-state"><div class="empty-glyph">◎</div><p>Click a node in the graph</p></div>';
    document.getElementById('detail-title').textContent = 'Select a node';
    activateSideTab('details');
  });
}

// ── Startup: check if already initialized ───────────────────────

(async function boot() {
  try {
    const res = await fetch('/api/status');
    const data = await res.json();
    if (data.initialized) {
      showDashboard(data.project_path);
      if (data.auto_accept) updateAutoAcceptBtn(true);
    } else {
      document.getElementById('setup-screen').classList.remove('hidden');
      setupBrowse('~');
    }
  } catch (e) {
    // Fallback: show setup screen on any error
    console.error('Boot error:', e);
    document.getElementById('setup-screen').classList.remove('hidden');
  }
})();
