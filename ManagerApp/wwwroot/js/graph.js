const state = new Map();

function sanitize(input) {
  if (!input) return "";
  return String(input)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/\"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function safeHostFromUrl(rawUrl) {
  try {
    const parsed = new URL(String(rawUrl || ""));
    return (parsed.hostname || "").toLowerCase();
  } catch {
    return "";
  }
}

function toRegistrableDomain(hostname) {
  const host = String(hostname || "").toLowerCase().trim();
  if (!host) return "unknown";
  const parts = host.split(".").filter(Boolean);
  if (parts.length <= 2) return parts.join(".");

  // Heuristic for ccTLD (e.g. gov.si -> keep last 3 when plausible)
  const last = parts[parts.length - 1];
  const second = parts[parts.length - 2];
  if (last.length === 2 && second.length <= 3 && parts.length >= 3) {
    return parts.slice(-3).join(".");
  }

  return parts.slice(-2).join(".");
}

function compactLabel(raw, max = 28) {
  const text = String(raw || "");
  if (text.length <= max) return text;
  return `${text.slice(0, max - 1)}...`;
}

function stableHash(text) {
  const raw = String(text || "");
  let hash = 2166136261;
  for (let i = 0; i < raw.length; i += 1) {
    hash ^= raw.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0) / 4294967295;
}

function extractTopic(url, domain) {
  const normalized = `${url || ""} ${domain || ""}`.toLowerCase();
  const topicSignals = {
    medicine: ["med", "health", "clinic", "hospital", "doctor", "disease", "who", "ema", "nijz"],
    government: ["gov", "uprava", "policy", "public", "parliament", "ministr"],
    media: ["news", "blog", "press", "article", "media"],
    education: ["edu", "school", "university", "faculty", "campus"],
    science: ["research", "science", "lab", "institute", "academy"],
    travel: ["tour", "travel", "visit", "hotel", "museum", "heritage"],
  };

  for (const [topic, signals] of Object.entries(topicSignals)) {
    if (signals.some((token) => normalized.includes(token))) {
      return topic;
    }
  }

  const host = (domain || "").toLowerCase().replace(/^www\./, "");
  const parts = host
    .split(".")
    .map((part) => part.trim())
    .filter((part) => part.length >= 3 && !["com", "org", "net", "eu", "si", "gov"].includes(part));
  if (parts.length > 0) {
    return parts[0];
  }

  const pathSegment = (url || "")
    .toLowerCase()
    .replace(/^https?:\/\//, "")
    .split("/")
    .slice(1)
    .map((part) => part.trim())
    .find((part) => part.length >= 4);

  return pathSegment || "generic";
}

function classifyNode(url, domain) {
  const normalized = `${url || ""} ${domain || ""}`.toLowerCase();
  const medicineSignals = ["med", "health", "clinic", "hospital", "doctor", "disease", "who", "ema", "nijz"];
  const govSignals = ["gov", "uprava", "policy", "public"];
  const mediaSignals = ["news", "blog", "press", "article"];

  const medicineScore = medicineSignals.reduce((acc, token) => acc + (normalized.includes(token) ? 1 : 0), 0);
  const govScore = govSignals.reduce((acc, token) => acc + (normalized.includes(token) ? 1 : 0), 0);
  const mediaScore = mediaSignals.reduce((acc, token) => acc + (normalized.includes(token) ? 1 : 0), 0);

  if (medicineScore >= 2) {
    return { topic: extractTopic(url, domain), score: clamp(0.55 + medicineScore * 0.1, 0.55, 1) };
  }

  if (govScore >= 1) {
    return { topic: extractTopic(url, domain), score: clamp(0.45 + govScore * 0.12, 0.45, 0.9) };
  }

  if (mediaScore >= 1) {
    return { topic: extractTopic(url, domain), score: clamp(0.35 + mediaScore * 0.1, 0.35, 0.8) };
  }

  return { topic: extractTopic(url, domain), score: 0.3 };
}

function hueForTopic(topic) {
  return Math.round(stableHash(topic || "generic") * 360);
}

function makeNodeColor(node) {
  if (node._isQueue) {
    return "hsl(210 7% 70%)";
  }

  const hue = hueForTopic(node._topic || "generic");
  const saturation = clamp(Math.round(24 + (node._score || 0) * 64), 24, 88);
  const lightness = clamp(Math.round(62 - (node._score || 0) * 20), 36, 62);
  return `hsl(${hue} ${saturation}% ${lightness}%)`;
}

function classifyIfQueued(node) {
  if (!node || !node._isQueue) {
    return;
  }

  const classification = classifyNode(node.url, node.domain);
  node._topic = classification.topic;
  node._score = classification.score;
  node._isQueue = false;
  node._classified = true;
}

function linkGradientId(graph, key) {
  const normalized = String(key || "link").replace(/[^a-zA-Z0-9_-]/g, "_");
  return `grad-${graph.hostId}-${normalized}`;
}

function edgeKey(sourceId, targetId) {
  return `${sourceId}->${targetId}`;
}

function pickSeedNodes(nodes) {
  const preferred = nodes.filter((node) => {
    const url = (node.url || "").toLowerCase();
    return url.includes("gov") || url.includes("nijz") || url.includes("kclj") || url.includes("who") || url.includes("ema");
  });

  if (preferred.length >= 3) {
    return preferred.slice(0, 8);
  }

  return [...nodes]
    .sort((a, b) => Number(b.size || 0) - Number(a.size || 0))
    .slice(0, Math.min(8, Math.max(3, Math.floor(nodes.length / 40) + 2)));
}

function renderLegend(host, queueMode) {
  const legend = document.createElement("div");
  legend.className = "graph-status-legend";
  legend.innerHTML = [
    `<span><i class="dot dot-seed"></i> seed</span>`,
    `<span><i class="dot dot-candidate"></i> queue</span>`,
    `<span><i class="dot dot-worker"></i> worker</span>`,
    `<span><i class="dot dot-scanned"></i> scanned/classified</span>`,
    `<span class="muted">queue mode: ${sanitize(queueMode || "server")}</span>`
  ].join("");
  host.appendChild(legend);
}

function parsePayload(payloadJson) {
  const payload = JSON.parse(payloadJson || "{}");
  const rawNodes = Array.isArray(payload.nodes)
    ? payload.nodes
    : (Array.isArray(payload.Nodes) ? payload.Nodes : []);
  const rawLinks = Array.isArray(payload.links)
    ? payload.links
    : (Array.isArray(payload.Links) ? payload.Links : []);

  const nodes = rawNodes
    .map((n) => ({
      id: Number(n.id ?? n.Id),
      url: n.url ?? n.Url ?? "",
      domain: n.domain ?? n.Domain ?? "unknown",
      pageType: n.pageType ?? n.PageType ?? "HTML",
      size: Number(n.size ?? n.Size ?? 1),
    }))
    .filter((n) => Number.isFinite(n.id));

  const links = rawLinks
    .map((l) => ({
      source: Number(l.source ?? l.Source),
      target: Number(l.target ?? l.Target),
    }))
    .filter((l) => Number.isFinite(l.source) && Number.isFinite(l.target));

  const rawWorkers = Array.isArray(payload.workers)
    ? payload.workers
    : (Array.isArray(payload.Workers) ? payload.Workers : []);

  const workers = rawWorkers
    .map((worker) => ({
      id: Number(worker.id ?? worker.Id),
      name: String(worker.name ?? worker.Name ?? ""),
      status: String(worker.status ?? worker.Status ?? "idle").toLowerCase(),
      currentUrl: worker.currentUrl ?? worker.CurrentUrl ?? null,
      currentNodeId: Number(worker.currentNodeId ?? worker.CurrentNodeId ?? NaN),
    }))
    .filter((worker) => Number.isFinite(worker.id) && worker.status === "active")
    .map((worker) => ({
      ...worker,
      currentNodeId: Number.isFinite(worker.currentNodeId) ? worker.currentNodeId : null,
    }));

  return {
    nodes,
    links,
    workers,
    visibleNodeLimit: nodes.length,
    queueMode: payload.queueMode || payload.QueueMode || "server",
  };
}

function initializeGraph(hostId, host, queueMode) {
  host.innerHTML = "";
  renderLegend(host, queueMode);
  const shell = host.closest(".graph-shell");
  let detailsPanel = shell ? shell.querySelector(".graph-details-panel") : null;
  if (!detailsPanel && shell) {
    detailsPanel = document.createElement("aside");
    detailsPanel.className = "graph-details-panel hidden";
    detailsPanel.innerHTML = "<div class='muted'>Click a node to inspect details.</div>";
    shell.appendChild(detailsPanel);
  }

  const width = host.clientWidth || 900;
  const height = host.clientHeight || 640;

  const svg = d3
    .select(host)
    .append("svg")
    .attr("width", width)
    .attr("height", height)
    .attr("class", "graph-svg");

  const defs = svg.append("defs");

  const canvas = svg.append("g");
  const clusterLayer = canvas.append("g").attr("class", "graph-clusters");
  const linkLayer = canvas.append("g").attr("class", "graph-links-base");
  const nodeLayer = canvas.append("g").attr("class", "graph-nodes");
  const seedLayer = canvas.append("g").attr("class", "graph-seeds");
  const workerLayer = canvas.append("g").attr("class", "graph-workers");

  const tooltip = d3
    .select(host)
    .append("div")
    .attr("class", "graph-tooltip")
    .style("opacity", 0);

  const zoom = d3.zoom().scaleExtent([0.15, 8]).on("zoom", (event) => {
    const graph = state.get(hostId);
    if (!graph) return;
    graph.currentTransform = event.transform;
    canvas.attr("transform", event.transform);
    updateVisibleSubset(graph);
    updateSelections(graph, pickSeedNodes(graph.visibleNodes));
    applyZoomDetail(graph, event.transform.k);
  });

  svg.call(zoom);

  const graph = {
    hostId,
    host,
    width,
    height,
    svg,
    defs,
    canvas,
    clusterLayer,
    linkLayer,
    nodeLayer,
    seedLayer,
    workerLayer,
    tooltip,
    zoom,
    currentTransform: d3.zoomIdentity,
    nodes: [],
    links: [],
    visibleNodes: [],
    visibleLinks: [],
    visibleNodeIds: new Set(),
    visibleNodeLimit: 500,
    nodeById: new Map(),
    linkByKey: new Map(),
    seedIds: new Set(),
    activeWorkers: [],
    workers: [],
    adjacency: new Map(),
    workerTimer: null,
    detailsPanel,
    selectedNodeId: null,
    hasInitialLayout: false,
    simulation: null,
    nodeSelection: null,
    linkSelection: null,
    seedSelection: null,
    workerSelection: null,
    clusterSelection: null,
    gradientSelection: null,
    groupNodePositions: new Map(),
    currentViewLevel: "item",
  };

  state.set(hostId, graph);
  return graph;
}

function getViewLevel(scale) {
  if (scale < 0.95) {
    return "domain";
  }

  return "item";
}

function groupKeyForNode(node, level) {
  const host = safeHostFromUrl(node.url) || String(node.domain || "").toLowerCase();
  if (level === "domain") {
    return `domain:${toRegistrableDomain(host)}`;
  }

  return `subdomain:${host || "unknown"}`;
}

function groupLabelForKey(key) {
  const [, label] = String(key || "").split(":", 2);
  return label || "unknown";
}

function buildRenderData(graph, level) {
  if (level === "item") {
    const linkData = graph.visibleLinks.map((link) => {
      const sourceId = linkSourceId(link);
      const targetId = linkTargetId(link);
      return {
        source: graph.nodeById.get(sourceId),
        target: graph.nodeById.get(targetId),
        _key: edgeKey(sourceId, targetId),
        _gradientId: linkGradientId(graph, edgeKey(sourceId, targetId)),
        _kind: "item",
      };
    }).filter((item) => item.source && item.target);

    const nodeData = graph.visibleNodes;
    for (const node of nodeData) {
      node._kind = "item";
    }
    return { nodeData, linkData };
  }

  const members = graph.visibleNodes;
  const groups = new Map();
  for (const node of members) {
    const key = groupKeyForNode(node, level);
    let group = groups.get(key);
    if (!group) {
      group = {
        id: key,
        label: groupLabelForKey(key),
        x: 0,
        y: 0,
        count: 0,
        totalScore: 0,
        queueCount: 0,
        members: [],
        _kind: level,
      };
      groups.set(key, group);
    }

    group.count += 1;
    group.totalScore += Number(node._score || 0);
    group.queueCount += node._isQueue ? 1 : 0;
    group.members.push(node.id);
    group.x += Number(node.x || graph.width / 2);
    group.y += Number(node.y || graph.height / 2);
  }

  const nodeData = [...groups.values()].map((group) => {
    const prev = graph.groupNodePositions.get(group.id);
    const centroidX = group.count > 0 ? group.x / group.count : graph.width / 2;
    const centroidY = group.count > 0 ? group.y / group.count : graph.height / 2;
    const stabilizedX = prev ? (prev.x * 0.65 + centroidX * 0.35) : centroidX;
    const stabilizedY = prev ? (prev.y * 0.65 + centroidY * 0.35) : centroidY;
    graph.groupNodePositions.set(group.id, { x: stabilizedX, y: stabilizedY });

    return {
      ...group,
      avgScore: group.count > 0 ? group.totalScore / group.count : 0,
      x: stabilizedX,
      y: stabilizedY,
    };
  });

  const nodeByGroup = new Map(nodeData.map((group) => [group.id, group]));
  const edgeMap = new Map();
  for (const link of graph.visibleLinks) {
    const sourceNode = graph.nodeById.get(linkSourceId(link));
    const targetNode = graph.nodeById.get(linkTargetId(link));
    if (!sourceNode || !targetNode) {
      continue;
    }

    const sourceGroup = groupKeyForNode(sourceNode, level);
    const targetGroup = groupKeyForNode(targetNode, level);
    if (sourceGroup === targetGroup) {
      continue;
    }

    const pair = sourceGroup < targetGroup
      ? `${sourceGroup}|${targetGroup}`
      : `${targetGroup}|${sourceGroup}`;

    const existing = edgeMap.get(pair);
    if (existing) {
      existing.weight += 1;
      continue;
    }

    edgeMap.set(pair, {
      _key: pair,
      _kind: level,
      source: nodeByGroup.get(sourceGroup),
      target: nodeByGroup.get(targetGroup),
      weight: 1,
    });
  }

  const linkData = [...edgeMap.values()].filter((edge) => edge.source && edge.target);
  return { nodeData, linkData };
}

function updateAggregateNodePositions(graph) {
  // When in aggregated view, recompute aggregate node positions from member nodes
  if (!graph.nodeSelection || graph.currentViewLevel === "item") {
    return;
  }

  const level = graph.currentViewLevel;
  const membersByGroup = new Map();
  
  // Group visible nodes by their aggregation key
  for (const node of graph.visibleNodes) {
    const key = groupKeyForNode(node, level);
    if (!membersByGroup.has(key)) {
      membersByGroup.set(key, []);
    }
    membersByGroup.get(key).push(node);
  }
  
  // Update each rendered node's position if it's an aggregate
  graph.nodeSelection.each(function(aggNode) {
    if (aggNode._kind !== "item" && membersByGroup.has(aggNode.id)) {
      const members = membersByGroup.get(aggNode.id);
      if (members.length > 0) {
        // Compute centroid of current member positions
        const centroidX = members.reduce((sum, m) => sum + Number(m.x || 0), 0) / members.length;
        const centroidY = members.reduce((sum, m) => sum + Number(m.y || 0), 0) / members.length;
        
        // Apply momentum smoothing to avoid jitter
        const prev = graph.groupNodePositions.get(aggNode.id);
        if (prev) {
          aggNode.x = prev.x * 0.65 + centroidX * 0.35;
          aggNode.y = prev.y * 0.65 + centroidY * 0.35;
        } else {
          aggNode.x = centroidX;
          aggNode.y = centroidY;
        }
        graph.groupNodePositions.set(aggNode.id, { x: aggNode.x, y: aggNode.y });
      }
    }
  });
}

function ensureSimulation(graph) {
  if (graph.simulation) {
    return;
  }

  graph.simulation = d3
    .forceSimulation(graph.nodes)
    .force("link", d3.forceLink(graph.links).id((d) => d.id).distance(58).strength(0.2))
    .force("charge", d3.forceManyBody().strength(-58).distanceMax(180))
    .force("center", d3.forceCenter(graph.width / 2, graph.height / 2))
    .force("collision", d3.forceCollide().radius((d) => 5 + Math.sqrt(Number(d.size || 1)) * 1.8))
    .velocityDecay(0.42)
    .alphaDecay(0.06);

  graph.simulation.on("tick", () => {
    // Update aggregate node positions before rendering
    updateAggregateNodePositions(graph);
    
    if (graph.linkSelection) {
      graph.linkSelection
        .attr("x1", (d) => d.source.x)
        .attr("y1", (d) => d.source.y)
        .attr("x2", (d) => d.target.x)
        .attr("y2", (d) => d.target.y);
    }

    if (graph.gradientSelection) {
      graph.gradientSelection
        .attr("x1", (d) => d.source.x || 0)
        .attr("y1", (d) => d.source.y || 0)
        .attr("x2", (d) => d.target.x || 0)
        .attr("y2", (d) => d.target.y || 0);
    }

    if (graph.nodeSelection) {
      graph.nodeSelection
        .attr("cx", (d) => d.x)
        .attr("cy", (d) => d.y);
    }

    if (graph.seedSelection) {
      graph.seedSelection
        .attr("cx", (d) => d.x)
        .attr("cy", (d) => d.y);
    }

    updateWorkerVisuals(graph);
  });
}

function seedRadius(node) {
  return Math.max(4, Math.min(28, 4 + Math.sqrt(Number(node.size || 1)) * 2.1));
}

function currentWorkerCentroid(graph) {
  if (!graph.workers.length) {
    return { x: graph.width / 2, y: graph.height / 2 };
  }

  const points = graph.workers
    .map((worker) => {
      const node = graph.nodeById.get(worker.frontierNodeId);
      if (!node) return null;
      return { x: Number(node.x || graph.width / 2), y: Number(node.y || graph.height / 2) };
    })
    .filter((point) => point !== null);

  if (!points.length) {
    return { x: graph.width / 2, y: graph.height / 2 };
  }

  const avgX = points.reduce((acc, p) => acc + p.x, 0) / points.length;
  const avgY = points.reduce((acc, p) => acc + p.y, 0) / points.length;
  return { x: avgX, y: avgY };
}

function applyInitialSeedLayout(graph, seedNodes, allNodes) {
  if (!seedNodes.length || allNodes.length === 0) {
    return;
  }

  const centerX = graph.width / 2;
  const centerY = graph.height / 2;
  const seedRadiusRing = Math.min(graph.width, graph.height) * 0.22;

  seedNodes.forEach((node, index) => {
    const angle = (index / seedNodes.length) * Math.PI * 2;
    const x = centerX + Math.cos(angle) * seedRadiusRing;
    const y = centerY + Math.sin(angle) * seedRadiusRing;
    node.x = x;
    node.y = y;
    node.vx = 0;
    node.vy = 0;
    node.fx = x;
    node.fy = y;
  });

  for (const node of allNodes) {
    if (graph.seedIds.has(node.id)) {
      continue;
    }

    const angle = stableHash(`n-angle-${node.id}`) * Math.PI * 2;
    const distance = seedRadiusRing * (0.45 + stableHash(`n-dist-${node.id}`));
    node.x = centerX + Math.cos(angle) * distance;
    node.y = centerY + Math.sin(angle) * distance;
    node.vx = 0;
    node.vy = 0;
  }

  setTimeout(() => {
    for (const node of seedNodes) {
      node.fx = null;
      node.fy = null;
    }
  }, 1500);
}

function mergeGraphData(graph, incomingNodes, incomingLinks) {
  let addedNodes = 0;
  let addedLinks = 0;
  const prevIds = new Set(graph.nodeById.keys());
  const seenIds = new Set();
  const spawnCenter = currentWorkerCentroid(graph);

  for (const raw of incomingNodes) {
    const id = raw.id;
    seenIds.add(id);
    const existing = graph.nodeById.get(id);
    const classification = classifyNode(raw.url, raw.domain);
    const inferredQueue = String(raw.pageType || "").toUpperCase() === "FRONTIER";

    if (existing) {
      existing.url = raw.url;
      existing.domain = raw.domain;
      existing.pageType = raw.pageType;
      existing.size = raw.size;
      existing._topic = classification.topic;
      existing._score = existing._isQueue ? existing._score : classification.score;
      if (!existing._classified) {
        existing._isQueue = inferredQueue;
      }
      continue;
    }

    const spawned = {
      ...raw,
      x: spawnCenter.x + (Math.random() - 0.5) * 36,
      y: spawnCenter.y + (Math.random() - 0.5) * 36,
      vx: 0,
      vy: 0,
      _topic: classification.topic,
      _score: classification.score,
      _visited: false,
      _seed: false,
      _isQueue: inferredQueue,
      _classified: !inferredQueue,
    };

    graph.nodes.push(spawned);
    graph.nodeById.set(id, spawned);
    addedNodes += 1;
  }

  const removedIds = [...prevIds].filter((id) => !seenIds.has(id));
  if (removedIds.length > 0) {
    const removeSet = new Set(removedIds);
    graph.nodes = graph.nodes.filter((node) => !removeSet.has(node.id));
    for (const id of removedIds) {
      graph.nodeById.delete(id);
      graph.seedIds.delete(id);
    }
  }

  const nextLinks = [];
  const nextKeySet = new Set();
  for (const link of incomingLinks) {
    if (!graph.nodeById.has(link.source) || !graph.nodeById.has(link.target)) {
      continue;
    }

    const key = edgeKey(link.source, link.target);
    nextKeySet.add(key);
    const existing = graph.linkByKey.get(key);
    if (existing) {
      nextLinks.push(existing);
      continue;
    }

    const created = { source: link.source, target: link.target, _key: key };
    graph.linkByKey.set(key, created);
    nextLinks.push(created);
    addedLinks += 1;
  }

  let removedLinks = 0;
  for (const key of [...graph.linkByKey.keys()]) {
    if (!nextKeySet.has(key)) {
      graph.linkByKey.delete(key);
      removedLinks += 1;
    }
  }

  graph.links = nextLinks;

  const seeds = pickSeedNodes(graph.nodes);
  graph.seedIds = new Set(seeds.map((node) => node.id));
  for (const node of graph.nodes) {
    node._seed = graph.seedIds.has(node.id);
  }

  if (!graph.hasInitialLayout) {
    applyInitialSeedLayout(graph, seeds, graph.nodes);
    graph.hasInitialLayout = true;
  }

  return {
    seeds,
    topologyChanged: addedNodes > 0 || removedIds.length > 0 || addedLinks > 0 || removedLinks > 0,
  };
}

function resolveCollapsedNodePositions(graph) {
  const nodes = graph.nodes || [];
  if (nodes.length < 24) {
    return false;
  }

  const buckets = new Map();
  for (const node of nodes) {
    if (!Number.isFinite(node.x) || !Number.isFinite(node.y)) {
      continue;
    }

    const key = `${Math.round(node.x)}:${Math.round(node.y)}`;
    if (!buckets.has(key)) {
      buckets.set(key, []);
    }
    buckets.get(key).push(node);
  }

  const crowded = [...buckets.values()].filter((group) => group.length >= 4);
  if (!crowded.length) {
    return false;
  }

  let moved = 0;
  for (const group of crowded) {
    const anchorX = Number(group[0].x || graph.width / 2);
    const anchorY = Number(group[0].y || graph.height / 2);
    const radiusBase = clamp(8 + Math.sqrt(group.length) * 2.6, 8, 42);

    group.forEach((node, index) => {
      if (index === 0) {
        return;
      }

      const angle = (index / group.length) * Math.PI * 2 + stableHash(`collapse-angle-${node.id}`) * 0.35;
      const radius = radiusBase * (0.35 + (index / Math.max(2, group.length - 1)) * 0.75);
      const x = anchorX + Math.cos(angle) * radius;
      const y = anchorY + Math.sin(angle) * radius;

      if (!Number.isFinite(x) || !Number.isFinite(y)) {
        return;
      }

      node.x = x;
      node.y = y;
      node.vx = (Number(node.vx) || 0) + (Math.cos(angle) * 0.3);
      node.vy = (Number(node.vy) || 0) + (Math.sin(angle) * 0.3);
      moved += 1;
    });
  }

  return moved > 0;
}

function buildAdjacency(graph) {
  const adjacency = new Map();
  for (const node of graph.nodes) {
    adjacency.set(node.id, []);
  }

  for (const link of graph.links) {
    const sourceId = Number(link.source.id ?? link.source);
    const targetId = Number(link.target.id ?? link.target);
    if (!adjacency.has(sourceId) || !adjacency.has(targetId)) {
      continue;
    }

    adjacency.get(sourceId).push(targetId);
    adjacency.get(targetId).push(sourceId);
  }

  return adjacency;
}

function linkSourceId(link) {
  return Number(link.source?.id ?? link.source);
}

function linkTargetId(link) {
  return Number(link.target?.id ?? link.target);
}

function updateVisibleSubset(graph) {
  const transform = graph.currentTransform || d3.zoomIdentity;
  const k = Number(transform.k || 1);
  const tx = Number(transform.x || 0);
  const ty = Number(transform.y || 0);
  const margin = 80;
  const limit = Math.max(50, Number(graph.visibleNodeLimit || 500));

  const inViewport = [];
  for (const node of graph.nodes) {
    const nx = Number(node.x ?? graph.width / 2);
    const ny = Number(node.y ?? graph.height / 2);
    const sx = nx * k + tx;
    const sy = ny * k + ty;
    if (sx >= -margin && sx <= graph.width + margin && sy >= -margin && sy <= graph.height + margin) {
      inViewport.push(node);
    }
  }

  const prioritizedInViewport = inViewport
    .slice()
    .sort((a, b) => Number(b.size || 0) - Number(a.size || 0));
  const visibleNodes = prioritizedInViewport.slice(0, limit);

  // Fill remaining slots from the global graph to keep the visible subset stable.
  if (visibleNodes.length < limit) {
    const selectedIds = new Set(visibleNodes.map((node) => node.id));
    const overflow = graph.nodes
      .slice()
      .sort((a, b) => Number(b.size || 0) - Number(a.size || 0));

    for (const node of overflow) {
      if (visibleNodes.length >= limit) {
        break;
      }

      if (selectedIds.has(node.id)) {
        continue;
      }

      selectedIds.add(node.id);
      visibleNodes.push(node);
    }
  }

  const visibleNodeIds = new Set(visibleNodes.map((node) => node.id));
  const visibleLinks = graph.links.filter((link) => {
    const sourceId = linkSourceId(link);
    const targetId = linkTargetId(link);
    return visibleNodeIds.has(sourceId) && visibleNodeIds.has(targetId);
  });

  graph.visibleNodes = visibleNodes;
  graph.visibleNodeIds = visibleNodeIds;
  graph.visibleLinks = visibleLinks;
}

function syncWorkers(graph, incomingWorkers) {
  graph.activeWorkers = incomingWorkers.slice();
}

function pickFrontierCandidates(graph, count) {
  if (count <= 0) {
    return [];
  }

  const queueNodes = graph.nodes.filter((node) => node._isQueue);
  const pool = queueNodes.length > 0 ? queueNodes : graph.nodes;
  return pool
    .slice()
    .sort((a, b) => {
      const scoreA = Number(a._score || 0) + Number(a.size || 0) * 0.05;
      const scoreB = Number(b._score || 0) + Number(b.size || 0) * 0.05;
      return scoreB - scoreA;
    })
    .slice(0, count);
}

function syncFrontierProxies(graph) {
  const count = graph.activeWorkers.length;
  const previousById = new Map(graph.workers.map((proxy) => [proxy.id, proxy]));
  const candidates = pickFrontierCandidates(graph, count);

  graph.workers = Array.from({ length: count }).map((_, index) => {
    const id = index + 1;
    const existing = previousById.get(id);
    const frontierNode = candidates[index] || null;
    const owner = graph.activeWorkers.find((worker) => worker.currentNodeId && frontierNode && worker.currentNodeId === frontierNode.id) || null;
    const targetX = frontierNode?.x ?? graph.width / 2;
    const targetY = frontierNode?.y ?? graph.height / 2;

    return {
      id,
      frontierNodeId: frontierNode?.id ?? null,
      ownerWorkerId: owner?.id ?? null,
      label: owner ? `W${owner.id}` : `Q${id}`,
      x: existing?.x ?? targetX,
      y: existing?.y ?? targetY,
      targetX,
      targetY,
    };
  });
}

function startWorkerTicker(graph) {
  if (graph.workerTimer) {
    return;
  }

  graph.workerTimer = d3.interval(() => {
    updateWorkerVisuals(graph);
  }, 90);
}

function updateWorkerVisuals(graph) {
  if (!graph.workerSelection) {
    return;
  }

  graph.workerSelection.attr("transform", (worker) => {
    const node = worker.frontierNodeId ? graph.nodeById.get(worker.frontierNodeId) : null;
    const targetX = (node?.x || graph.width / 2) + 12;
    const targetY = (node?.y || graph.height / 2) - 12;
    worker.targetX = targetX;
    worker.targetY = targetY;

    const currentX = Number.isFinite(worker.x) ? worker.x : targetX;
    const currentY = Number.isFinite(worker.y) ? worker.y : targetY;
    worker.x = currentX + (targetX - currentX) * 0.16;
    worker.y = currentY + (targetY - currentY) * 0.16;

    return `translate(${worker.x}, ${worker.y})`;
  });
}

function connectedComponents(graph) {
  const adjacency = buildAdjacency(graph);
  const seen = new Set();
  const components = [];

  for (const node of graph.nodes) {
    if (seen.has(node.id)) continue;
    const stack = [node.id];
    const ids = [];

    while (stack.length) {
      const next = stack.pop();
      if (seen.has(next)) continue;
      seen.add(next);
      ids.push(next);
      for (const nb of adjacency.get(next) || []) {
        if (!seen.has(nb)) {
          stack.push(nb);
        }
      }
    }

    components.push(ids);
  }

  return components;
}

function updateClusterIslands(graph) {
  const comps = connectedComponents(graph);
  const clusters = comps.map((ids, idx) => {
    const points = ids
      .map((id) => graph.nodeById.get(id))
      .filter((node) => node && Number.isFinite(node.x) && Number.isFinite(node.y));
    if (!points.length) {
      return null;
    }

    const cx = points.reduce((acc, node) => acc + node.x, 0) / points.length;
    const cy = points.reduce((acc, node) => acc + node.y, 0) / points.length;
    const r = clamp(8 + Math.sqrt(points.length) * 6, 14, 72);

    return {
      id: `cluster-${idx}`,
      cx,
      cy,
      r,
      count: points.length,
    };
  }).filter((cluster) => cluster !== null);

  const groups = graph.clusterLayer
    .selectAll("g")
    .data(clusters, (d) => d.id)
    .join((enter) => {
      const g = enter.append("g");
      g.append("circle");
      g.append("text");
      return g;
    });

  groups
    .select("circle")
    .attr("cx", (d) => d.cx)
    .attr("cy", (d) => d.cy)
    .attr("r", (d) => d.r)
    .attr("fill", "rgba(52, 95, 147, 0.18)")
    .attr("stroke", "rgba(52, 95, 147, 0.48)")
    .attr("stroke-width", 1.5);

  groups
    .select("text")
    .attr("x", (d) => d.cx)
    .attr("y", (d) => d.cy + 4)
    .attr("text-anchor", "middle")
    .attr("font-size", 11)
    .attr("font-weight", 700)
    .attr("fill", "#1f3a5f")
    .text((d) => d.count);

  graph.clusterSelection = groups;
}

function applyZoomDetail(graph, scale) {
  if (!graph.linkSelection || !graph.nodeSelection) {
    return;
  }

  graph.currentViewLevel = getViewLevel(scale);

  graph.clusterLayer.style("display", "none");
  graph.linkLayer.style("display", null);
  graph.nodeLayer.style("display", null);
  graph.seedLayer.style("display", null);
  graph.workerLayer.style("display", null);

  graph.linkSelection.attr("display", null);

  graph.nodeSelection
    .attr("opacity", 1)
    .attr("r", (d) => {
      if (d._kind === "item") {
        return seedRadius(d);
      }
      return clamp(12 + Math.sqrt(Number(d.count || 1)) * 4.2, 14, 48);
    });
}

function applyLinkStyles(graph) {
  if (!graph.linkSelection) {
    return;
  }

  if (graph.gradientSelection) {
    graph.gradientSelection.each(function(d) {
      const gradient = d3.select(this);
      gradient.select("stop[offset='0%']").attr("stop-color", makeNodeColor(d.source));
      gradient.select("stop[offset='100%']").attr("stop-color", makeNodeColor(d.target));
    });
  }

  graph.linkSelection
    .attr("stroke", (d) => {
      const targetQueue = d.target?._isQueue;
      if (targetQueue) {
        return "hsl(210 7% 66%)";
      }
      return `url(#${d._gradientId})`;
    })
    .attr("stroke-width", (d) => (d.target?._isQueue ? 0.65 : 1.15))
    .attr("stroke-opacity", (d) => (d.target?._isQueue ? 0.75 : 0.52));
}

function renderNodeDetails(graph, node) {
  if (!graph.detailsPanel) {
    return;
  }

  if (!node) {
    graph.detailsPanel.classList.add("hidden");
    graph.detailsPanel.innerHTML = "<div class='muted'>Click a node to inspect details.</div>";
    return;
  }

  if (node._kind === "domain" || node._kind === "subdomain") {
    graph.detailsPanel.classList.remove("hidden");
    graph.detailsPanel.innerHTML = [
      `<h3>${sanitize(node.label || "group")}</h3>`,
      `<p><strong>View:</strong> ${sanitize(node._kind)}</p>`,
      `<p><strong>Grouped pages:</strong> ${sanitize(node.count || 0)}</p>`,
      `<p><strong>Queue candidates:</strong> ${sanitize(node.queueCount || 0)}</p>`,
      `<p><strong>Average score:</strong> ${sanitize((node.avgScore || 0).toFixed(2))}</p>`,
      `<p class='text-small text-muted'>Zoom or click to drill down.</p>`,
    ].join("");
    return;
  }

  graph.detailsPanel.classList.remove("hidden");
  const neighbors = graph.adjacency.get(node.id) || [];
  graph.detailsPanel.innerHTML = [
    `<h3>${sanitize(node.domain || "unknown")}</h3>`,
    `<p><strong>URL:</strong> <a href="${sanitize(node.url)}" target="_blank" rel="noreferrer">${sanitize(node.url)}</a></p>`,
    `<p><strong>Page type:</strong> ${sanitize(node.pageType || "HTML")}</p>`,
    `<p><strong>Backlinks:</strong> ${sanitize(node.size || 0)}</p>`,
    `<p><strong>Connected nodes:</strong> ${sanitize(neighbors.length)}</p>`,
  ].join("");
}

function applySelectionFocus(graph) {
  const selectedId = graph.selectedNodeId;
  if (!selectedId) {
    if (graph.nodeSelection) {
      graph.nodeSelection
        .attr("opacity", 1)
        .attr("stroke", (d) => d._kind === "item" ? (d._seed ? "#d58a00" : "#ffffff") : "#1f3a5f")
        .attr("stroke-width", (d) => d._kind === "item" ? (d._seed ? 2.2 : 1.2) : 2.2);
    }
    if (graph.linkSelection) {
      graph.linkSelection
        .attr("opacity", 1)
        .attr("stroke-opacity", (d) => (d.target?._isQueue ? 0.75 : 0.52));
      applyLinkStyles(graph);
    }
    renderNodeDetails(graph, null);
    return;
  }

  if (graph.currentViewLevel !== "item") {
    if (graph.nodeSelection) {
      graph.nodeSelection
        .attr("opacity", (d) => (d.id === selectedId ? 1 : 0.3))
        .attr("stroke", (d) => (d.id === selectedId ? "#ff7f0e" : "#1f3a5f"))
        .attr("stroke-width", (d) => (d.id === selectedId ? 3.4 : 2));
    }

    if (graph.linkSelection) {
      graph.linkSelection
        .attr("opacity", (d) => (d.source.id === selectedId || d.target.id === selectedId ? 0.95 : 0.12))
        .attr("stroke-opacity", (d) => (d.source.id === selectedId || d.target.id === selectedId ? 0.95 : 0.12));
    }

    const selectedNode = graph.nodeSelection?.data().find((node) => node.id === selectedId) || null;
    renderNodeDetails(graph, selectedNode);
    return;
  }

  if (!graph.visibleNodeIds.has(selectedId)) {
    graph.selectedNodeId = null;
    applySelectionFocus(graph);
    return;
  }

  const neighbors = new Set(graph.adjacency.get(selectedId) || []);
  neighbors.add(selectedId);

  if (graph.nodeSelection) {
    graph.nodeSelection
      .attr("opacity", (d) => (neighbors.has(d.id) ? 1 : 0.2))
      .attr("stroke", (d) => {
        if (d.id === selectedId) {
          return "#ff7f0e";
        }
        if (neighbors.has(d.id)) {
          return "#1f3a5f";
        }
        return d._seed ? "#d58a00" : "#ffffff";
      })
      .attr("stroke-width", (d) => (d.id === selectedId ? 3.4 : neighbors.has(d.id) ? 2.2 : d._seed ? 2.2 : 1.2));
  }

  if (graph.linkSelection) {
    graph.linkSelection
      .attr("opacity", (d) => {
        const sourceId = Number(d.source.id ?? d.source);
        const targetId = Number(d.target.id ?? d.target);
        return sourceId === selectedId || targetId === selectedId ? 1 : 0.08;
      })
      .attr("stroke-opacity", (d) => {
        const sourceId = Number(d.source.id ?? d.source);
        const targetId = Number(d.target.id ?? d.target);
        if (sourceId === selectedId || targetId === selectedId) {
          return 0.95;
        }
        return 0.08;
      });
  }

  renderNodeDetails(graph, graph.nodeById.get(selectedId));
}

function updateSelections(graph, seedNodes) {
  const visibleSeedIds = new Set(seedNodes.map((node) => node.id));

  const level = getViewLevel(graph.currentTransform?.k || 1);
  graph.currentViewLevel = level;
  const { nodeData, linkData } = buildRenderData(graph, level);

  graph.gradientSelection = graph.defs
    .selectAll("linearGradient")
    .data(level === "item" ? linkData : [], (d) => d._key)
    .join((enter) => {
      const gradient = enter
        .append("linearGradient")
        .attr("gradientUnits", "userSpaceOnUse");
      gradient.append("stop").attr("offset", "0%");
      gradient.append("stop").attr("offset", "100%");
      return gradient;
    })
    .attr("id", (d) => d._gradientId)
    .attr("x1", (d) => d.source.x || 0)
    .attr("y1", (d) => d.source.y || 0)
    .attr("x2", (d) => d.target.x || 0)
    .attr("y2", (d) => d.target.y || 0);

  if (level === "item") {
    graph.gradientSelection.each(function(d) {
      const gradient = d3.select(this);
      gradient.select("stop[offset='0%']").attr("stop-color", makeNodeColor(d.source));
      gradient.select("stop[offset='100%']").attr("stop-color", makeNodeColor(d.target));
    });
  }

  graph.linkSelection = graph.linkLayer
    .selectAll("line")
    .data(linkData, (d) => d._key)
    .join("line");

  if (level === "item") {
    applyLinkStyles(graph);
  } else {
    graph.linkSelection
      .attr("stroke", "#5e7697")
      .attr("stroke-width", (d) => clamp(1 + Math.log10((d.weight || 1) + 1), 1, 4))
      .attr("stroke-opacity", 0.55);
  }

  graph.nodeSelection = graph.nodeLayer
    .selectAll("circle")
    .data(nodeData, (d) => d.id)
    .join("circle")
    .attr("r", (d) => d._kind === "item"
      ? seedRadius(d)
      : clamp(12 + Math.sqrt(Number(d.count || 1)) * 4.2, 14, 48))
    .attr("fill", (d) => {
      if (d._kind === "item") {
        return makeNodeColor(d);
      }

      return d._kind === "domain" ? "hsl(204 42% 58%)" : "hsl(164 42% 52%)";
    })
    .attr("stroke", (d) => d._kind === "item" ? (d._seed ? "#d58a00" : "#ffffff") : "#1f3a5f")
    .attr("stroke-width", (d) => d._kind === "item" ? (d._seed ? 2.2 : 1.2) : 2.2)
    .call(
      d3
        .drag()
        .on("start", (event, d) => {
          if (d._kind !== "item") {
            return;
          }
          if (!event.active) graph.simulation.alphaTarget(0.3).restart();
          d.fx = d.x;
          d.fy = d.y;
        })
        .on("drag", (event, d) => {
          if (d._kind !== "item") {
            return;
          }
          d.fx = event.x;
          d.fy = event.y;
        })
        .on("end", (event, d) => {
          if (d._kind !== "item") {
            return;
          }
          if (!event.active) graph.simulation.alphaTarget(0);
          d.fx = null;
          d.fy = null;
        })
    );

  graph.nodeSelection
    .on("mouseover", (_, d) => {
      graph.tooltip
        .style("opacity", 1)
        .html(
          d._kind === "item"
            ? (`<div><strong>${sanitize(d.domain || "unknown")}</strong></div>` +
              `<div>${sanitize(d.url)}</div>` +
              `<div>backlinks: ${sanitize(d.size || 0)}</div>` +
              `<div>score: ${sanitize((d._score || 0).toFixed(2))}</div>`)
            : (`<div><strong>${sanitize(d.label || "group")}</strong></div>` +
              `<div>grouped pages: ${sanitize(d.count || 0)}</div>` +
              `<div>avg score: ${sanitize((d.avgScore || 0).toFixed(2))}</div>`)
        );
    })
    .on("mousemove", (event) => {
      graph.tooltip.style("left", `${event.offsetX + 12}px`).style("top", `${event.offsetY + 12}px`);
    })
    .on("mouseout", () => graph.tooltip.style("opacity", 0))
    .on("click", (_, d) => {
      if (d._kind !== "item") {
        const nextScale = d._kind === "domain" ? 1.0 : 2.0;
        const targetTransform = d3.zoomIdentity
          .translate(graph.width / 2 - (d.x || graph.width / 2) * nextScale, graph.height / 2 - (d.y || graph.height / 2) * nextScale)
          .scale(nextScale);

        graph.currentTransform = targetTransform;
        graph.selectedNodeId = d.id;
        graph.svg
          .transition()
          .duration(360)
          .call(graph.zoom.transform, targetTransform);
        return;
      }

      graph.selectedNodeId = graph.selectedNodeId === d.id ? null : d.id;
      applySelectionFocus(graph);
    });

  graph.seedSelection = graph.seedLayer
    .selectAll("circle")
    .data(level === "item" ? graph.visibleNodes.filter((node) => visibleSeedIds.has(node.id)) : [], (d) => d.id)
    .join("circle")
    .attr("r", (d) => seedRadius(d) + 4)
    .attr("fill", "none")
    .attr("stroke", "#f2b84b")
    .attr("stroke-width", 2.2)
    .attr("stroke-dasharray", "3 2");

  graph.workerSelection = graph.workerLayer
    .selectAll("g")
    .data(level === "item" ? graph.workers : [], (d) => d.id)
    .join((enter) => {
      const group = enter.append("g").attr("class", "graph-worker");
      group
        .append("circle")
        .attr("r", 9)
        .attr("fill", "#1f3a5f")
        .attr("stroke", "#ffffff")
        .attr("stroke-width", 1.5);
      group
        .append("text")
        .attr("text-anchor", "middle")
        .attr("dominant-baseline", "central")
        .attr("font-size", 9)
        .attr("font-weight", "700")
        .attr("fill", "#ffffff")
        .text((d) => d.label || `Q${d.id}`);
      return group;
    });

  graph.workerSelection.select("text").text((d) => d.label || `Q${d.id}`);

  const groupLabels = graph.nodeLayer
    .selectAll("text.graph-group-label")
    .data(level === "item" ? [] : nodeData, (d) => d.id)
    .join("text")
    .attr("class", "graph-group-label")
    .attr("text-anchor", "middle")
    .attr("dominant-baseline", "middle")
    .attr("font-size", 11)
    .attr("font-weight", 700)
    .attr("fill", "#ffffff")
    .style("pointer-events", "none")
    .text((d) => `${compactLabel(d.label, 18)} (${d.count})`)
    .attr("x", (d) => d.x)
    .attr("y", (d) => d.y);

  if (level === "item") {
    graph.nodeLayer.selectAll("text.graph-group-label").remove();
  }

  applySelectionFocus(graph);
  updateWorkerVisuals(graph);
}

export function renderGraph(hostId, payloadJson) {
  const host = document.getElementById(hostId);
  if (!host) return;

  const payload = parsePayload(payloadJson);
  if (payload.nodes.length === 0) {
    return;
  }

  if (typeof d3 === "undefined") {
    host.innerHTML = `<div class=\"text-small text-muted\">D3 library unavailable.</div>`;
    return;
  }

  let graph = state.get(hostId);
  if (!graph) {
    graph = initializeGraph(hostId, host, payload.queueMode);
  }

  graph.visibleNodeLimit = Math.max(50, Number(payload.visibleNodeLimit || graph.visibleNodeLimit || 500));

  const mergeResult = mergeGraphData(graph, payload.nodes, payload.links);
  graph.adjacency = buildAdjacency(graph);
  syncWorkers(graph, payload.workers || []);
  syncFrontierProxies(graph);
  const layoutRecovered = resolveCollapsedNodePositions(graph);
  updateVisibleSubset(graph);
  ensureSimulation(graph);
  updateSelections(graph, mergeResult.seeds);
  startWorkerTicker(graph);

  const linkForce = graph.simulation.force("link");
  linkForce.links(graph.links);

  graph.simulation.nodes(graph.nodes);
  if (mergeResult.topologyChanged || layoutRecovered) {
    graph.simulation.alpha(layoutRecovered ? 0.32 : 0.2).restart();
  }

  graph.svg.call(graph.zoom.transform, graph.currentTransform || d3.zoomIdentity);
  applyZoomDetail(graph, graph.currentTransform?.k || 1);
}

export function focusNode(hostId, term) {
  const graph = state.get(hostId);
  if (!graph || !term || !graph.nodeSelection) {
    return;
  }

  const matchTerm = term.toLowerCase();
  const match = graph.nodes.find((node) => {
    return (node.url || "").toLowerCase().includes(matchTerm)
      || (node.domain || "").toLowerCase().includes(matchTerm);
  });

  graph.selectedNodeId = match ? match.id : null;
  applySelectionFocus(graph);

  if (!match) {
    return;
  }

  const transform = d3.zoomIdentity
    .translate(graph.width / 2 - (match.x || 0) * 1.5, graph.height / 2 - (match.y || 0) * 1.5)
    .scale(1.5);

  graph.currentTransform = transform;
  graph.svg
    .transition()
    .duration(420)
    .call(graph.zoom.transform, transform);
}

export function exportSvg(hostId, filename) {
  const host = document.getElementById(hostId);
  if (!host) return;

  const svg = host.querySelector("svg");
  if (!svg) return;

  const serializer = new XMLSerializer();
  const source = serializer.serializeToString(svg);
  const blob = new Blob([source], { type: "image/svg+xml;charset=utf-8" });
  const url = URL.createObjectURL(blob);

  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename || "crawler-graph.svg";
  document.body.appendChild(anchor);
  anchor.click();
  document.body.removeChild(anchor);

  URL.revokeObjectURL(url);
}

export function setViewLevel(hostId, level) {
  const graph = state.get(hostId);
  if (!graph) {
    return;
  }

  const normalized = String(level || "").toLowerCase();
  const targetScale = normalized === "site" ? 0.45 : 2;
  const current = graph.currentTransform || d3.zoomIdentity;
  const centerWorldX = (graph.width / 2 - current.x) / current.k;
  const centerWorldY = (graph.height / 2 - current.y) / current.k;

  const targetTransform = d3.zoomIdentity
    .translate(graph.width / 2 - centerWorldX * targetScale, graph.height / 2 - centerWorldY * targetScale)
    .scale(targetScale);

  graph.currentTransform = targetTransform;
  graph.svg
    .transition()
    .duration(280)
    .call(graph.zoom.transform, targetTransform);
}
