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

  return {
    nodes,
    links,
    queueMode: payload.queueMode || payload.QueueMode || "server",
  };
}

function initializeGraph(hostId, host, queueMode) {
  host.innerHTML = "";
  renderLegend(host, queueMode);

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
    nodeById: new Map(),
    linkByKey: new Map(),
    seedIds: new Set(),
    workers: [],
    workerTimer: null,
    simulation: null,
    nodeSelection: null,
    linkSelection: null,
    seedSelection: null,
    workerSelection: null,
    clusterSelection: null,
    gradientSelection: null,
  };

  state.set(hostId, graph);
  return graph;
}

function ensureSimulation(graph) {
  if (graph.simulation) {
    return;
  }

  graph.simulation = d3
    .forceSimulation(graph.nodes)
    .force("link", d3.forceLink(graph.links).id((d) => d.id).distance(62).strength(0.18))
    .force("charge", d3.forceManyBody().strength(-130))
    .force("center", d3.forceCenter(graph.width / 2, graph.height / 2))
    .force("collision", d3.forceCollide().radius((d) => 5 + Math.sqrt(Number(d.size || 1)) * 2.1));

  graph.simulation.on("tick", () => {
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
      const node = graph.nodeById.get(worker.currentNodeId);
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
  }

  for (const key of [...graph.linkByKey.keys()]) {
    if (!nextKeySet.has(key)) {
      graph.linkByKey.delete(key);
    }
  }

  graph.links = nextLinks;

  const seeds = pickSeedNodes(graph.nodes);
  graph.seedIds = new Set(seeds.map((node) => node.id));
  for (const node of graph.nodes) {
    node._seed = graph.seedIds.has(node.id);
  }

  const isFirstDataLoad = graph.workers.length === 0;
  if (isFirstDataLoad) {
    applyInitialSeedLayout(graph, seeds, graph.nodes);
  }

  return seeds;
}

function ensureWorkers(graph, seedNodes) {
  const desired = clamp(Math.floor(seedNodes.length / 2) || 2, 2, 5);
  const queueNodes = graph.nodes.filter((node) => node._isQueue);
  const pickStart = (index) => {
    const source = queueNodes.length > 0 ? queueNodes : graph.nodes;
    return source[index % Math.max(source.length, 1)] || null;
  };

  if (graph.workers.length === 0) {
    graph.workers = Array.from({ length: desired }).map((_, index) => {
      const seed = pickStart(index);
      return {
        id: index + 1,
        currentNodeId: seed ? seed.id : null,
      };
    });
    return;
  }

  if (graph.workers.length < desired) {
    const start = graph.workers.length;
    for (let i = start; i < desired; i += 1) {
      const seed = pickStart(i);
      graph.workers.push({
        id: i + 1,
        currentNodeId: seed ? seed.id : null,
      });
    }
  }
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

function stepWorkers(graph) {
  if (!graph.nodes.length || !graph.workers.length) {
    return;
  }

  const adjacency = buildAdjacency(graph);
  for (const worker of graph.workers) {
    const currentId = worker.currentNodeId;
    if (!currentId || !adjacency.has(currentId)) {
      const queuePool = graph.nodes.filter((node) => node._isQueue);
      const sourcePool = queuePool.length > 0 ? queuePool : graph.nodes;
      const fallback = sourcePool[Math.floor(Math.random() * sourcePool.length)];
      worker.currentNodeId = fallback ? fallback.id : null;
      continue;
    }

    classifyIfQueued(graph.nodeById.get(currentId));

    const neighbors = adjacency.get(currentId);
    if (!neighbors.length) {
      continue;
    }

    const queueFirst = neighbors.filter((id) => graph.nodeById.get(id)?._isQueue);
    const sourcePool = queueFirst.length > 0 ? queueFirst : neighbors;
    const sorted = [...sourcePool].sort((a, b) => {
      const scoreA = graph.nodeById.get(a)?._score ?? 0;
      const scoreB = graph.nodeById.get(b)?._score ?? 0;
      return scoreB - scoreA;
    });

    const pick = sorted[Math.floor(stableHash(`worker-${worker.id}-${Date.now()}`) * Math.min(4, sorted.length))];
    worker.currentNodeId = pick;
    classifyIfQueued(graph.nodeById.get(pick));
  }

  if (graph.nodeSelection) {
    graph.nodeSelection
      .attr("fill", (d) => makeNodeColor(d))
      .attr("stroke", (d) => {
        if (graph.workers.some((worker) => worker.currentNodeId === d.id)) {
          return "#123a63";
        }
        return d._seed ? "#d58a00" : "#ffffff";
      })
      .attr("stroke-width", (d) => {
        if (graph.workers.some((worker) => worker.currentNodeId === d.id)) {
          return 2.8;
        }
        return d._seed ? 2.2 : 1.2;
      });
  }

  if (graph.linkSelection) {
    applyLinkStyles(graph);
  }

  updateWorkerVisuals(graph);
}

function updateWorkerVisuals(graph) {
  if (!graph.workerSelection) {
    return;
  }

  graph.workerSelection.attr("transform", (worker) => {
    const node = graph.nodeById.get(worker.currentNodeId);
    if (!node) {
      return `translate(${graph.width / 2}, ${graph.height / 2})`;
    }
    return `translate(${(node.x || graph.width / 2) + 12}, ${(node.y || graph.height / 2) - 12})`;
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

  graph.clusterLayer.style("display", "none");
  graph.linkLayer.style("display", null);
  graph.nodeLayer.style("display", null);
  graph.seedLayer.style("display", null);
  graph.workerLayer.style("display", null);

  graph.linkSelection.attr("display", null);

  graph.nodeSelection
    .attr("opacity", 1)
    .attr("r", (d) => seedRadius(d));
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

function updateSelections(graph, seedNodes) {
  const linkData = graph.links.map((link) => {
    const sourceId = Number(link.source.id ?? link.source);
    const targetId = Number(link.target.id ?? link.target);
    return {
      source: graph.nodeById.get(sourceId),
      target: graph.nodeById.get(targetId),
      _key: edgeKey(sourceId, targetId),
      _gradientId: linkGradientId(graph, edgeKey(sourceId, targetId)),
    };
  }).filter((item) => item.source && item.target);

  graph.gradientSelection = graph.defs
    .selectAll("linearGradient")
    .data(linkData, (d) => d._key)
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

  graph.gradientSelection.each(function(d) {
    const gradient = d3.select(this);
    gradient.select("stop[offset='0%']").attr("stop-color", makeNodeColor(d.source));
    gradient.select("stop[offset='100%']").attr("stop-color", makeNodeColor(d.target));
  });

  graph.linkSelection = graph.linkLayer
    .selectAll("line")
    .data(linkData, (d) => d._key)
    .join("line");

  applyLinkStyles(graph);

  graph.nodeSelection = graph.nodeLayer
    .selectAll("circle")
    .data(graph.nodes, (d) => d.id)
    .join("circle")
    .attr("r", (d) => seedRadius(d))
    .attr("fill", (d) => makeNodeColor(d))
    .attr("stroke", (d) => d._seed ? "#d58a00" : "#ffffff")
    .attr("stroke-width", (d) => d._seed ? 2.2 : 1.2)
    .call(
      d3
        .drag()
        .on("start", (event, d) => {
          if (!event.active) graph.simulation.alphaTarget(0.3).restart();
          d.fx = d.x;
          d.fy = d.y;
        })
        .on("drag", (event, d) => {
          d.fx = event.x;
          d.fy = event.y;
        })
        .on("end", (event, d) => {
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
          `<div><strong>${sanitize(d.domain || "unknown")}</strong></div>` +
            `<div>${sanitize(d.url)}</div>` +
            `<div>backlinks: ${sanitize(d.size || 0)}</div>` +
            `<div>score: ${sanitize((d._score || 0).toFixed(2))}</div>`
        );
    })
    .on("mousemove", (event) => {
      graph.tooltip.style("left", `${event.offsetX + 12}px`).style("top", `${event.offsetY + 12}px`);
    })
    .on("mouseout", () => graph.tooltip.style("opacity", 0));

  graph.seedSelection = graph.seedLayer
    .selectAll("circle")
    .data(seedNodes, (d) => d.id)
    .join("circle")
    .attr("r", (d) => seedRadius(d) + 4)
    .attr("fill", "none")
    .attr("stroke", "#f2b84b")
    .attr("stroke-width", 2.2)
    .attr("stroke-dasharray", "3 2");

  graph.workerSelection = graph.workerLayer
    .selectAll("g")
    .data(graph.workers, (d) => d.id)
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
        .text((d) => `W${d.id}`);
      return group;
    });

  updateWorkerVisuals(graph);
}

function startWorkerTicker(graph) {
  if (graph.workerTimer) {
    return;
  }

  graph.workerTimer = d3.interval(() => {
    stepWorkers(graph);
  }, 1400);
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

  const seedNodes = mergeGraphData(graph, payload.nodes, payload.links);
  ensureWorkers(graph, seedNodes);
  ensureSimulation(graph);
  updateSelections(graph, seedNodes);
  startWorkerTicker(graph);

  const linkForce = graph.simulation.force("link");
  linkForce.links(graph.links);

  graph.simulation.nodes(graph.nodes);
  graph.simulation.alpha(0.35).restart();

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

  graph.nodeSelection
    .attr("stroke", (d) => (match && d.id === match.id ? "#ff7f0e" : d._seed ? "#d58a00" : "#ffffff"))
    .attr("stroke-width", (d) => (match && d.id === match.id ? 3.8 : d._seed ? 2.2 : 1.2));

  if (!match) return;

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
