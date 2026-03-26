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

function classifyNode(url) {
  const normalized = (url || "").toLowerCase();
  const medicineSignals = ["med", "health", "clinic", "hospital", "doctor", "disease", "who", "ema", "nijz"];
  const govSignals = ["gov", "uprava", "policy", "public"];
  const mediaSignals = ["news", "blog", "press", "article"];

  const medicineScore = medicineSignals.reduce((acc, token) => acc + (normalized.includes(token) ? 1 : 0), 0);
  const govScore = govSignals.reduce((acc, token) => acc + (normalized.includes(token) ? 1 : 0), 0);
  const mediaScore = mediaSignals.reduce((acc, token) => acc + (normalized.includes(token) ? 1 : 0), 0);

  if (medicineScore >= 2) {
    return { category: "medicine", score: clamp(0.55 + medicineScore * 0.1, 0.55, 1) };
  }

  if (govScore >= 1) {
    return { category: "government", score: clamp(0.45 + govScore * 0.12, 0.45, 0.9) };
  }

  if (mediaScore >= 1) {
    return { category: "media", score: clamp(0.35 + mediaScore * 0.1, 0.35, 0.8) };
  }

  return { category: "generic", score: 0.3 };
}

function hueForCategory(category) {
  switch (category) {
    case "medicine":
      return 145;
    case "government":
      return 210;
    case "media":
      return 35;
    default:
      return 265;
  }
}

function makeNodeColor(node) {
  if (node._isCandidate) {
    const lightness = 76 - Math.round((node._candidatePriority || 1) * 1.8);
    return `hsl(0 0% ${clamp(lightness, 45, 78)}%)`;
  }

  if (!node._visited) {
    return "hsl(195 16% 88%)";
  }

  const hue = hueForCategory(node._category || "generic");
  const saturation = clamp(Math.round(24 + (node._score || 0) * 64), 24, 88);
  const lightness = clamp(Math.round(62 - (node._score || 0) * 20), 36, 62);
  return `hsl(${hue} ${saturation}% ${lightness}%)`;
}

function edgeKey(sourceId, targetId) {
  return `${sourceId}->${targetId}`;
}

function clearExisting(hostId) {
  const existing = state.get(hostId);
  if (!existing) {
    return;
  }

  if (existing.simulation) {
    existing.simulation.stop();
  }

  if (existing.workerTimer) {
    existing.workerTimer.stop();
  }

  state.delete(hostId);
}

function pickSeedNodes(nodes) {
  const preferred = nodes.filter((node) => {
    const url = (node.url || "").toLowerCase();
    return url.includes("gov") || url.includes("nijz") || url.includes("kclj") || url.includes("who") || url.includes("ema");
  });

  if (preferred.length >= 3) {
    return preferred.slice(0, 6);
  }

  return [...nodes]
    .sort((a, b) => Number(b.size || 0) - Number(a.size || 0))
    .slice(0, Math.min(6, Math.max(2, Math.floor(nodes.length / 40) + 2)));
}

function renderLegend(host, queueMode) {
  const legend = document.createElement("div");
  legend.className = "graph-status-legend";
  legend.innerHTML = [
    `<span><i class="dot dot-seed"></i> seed</span>`,
    `<span><i class="dot dot-candidate"></i> candidate</span>`,
    `<span><i class="dot dot-worker"></i> worker</span>`,
    `<span><i class="dot dot-scanned"></i> scanned/classified</span>`,
    `<span class="muted">queue mode: ${sanitize(queueMode || "server")}</span>`
  ].join("");
  host.appendChild(legend);
}

export function renderGraph(hostId, payloadJson) {
  const host = document.getElementById(hostId);
  if (!host || typeof d3 === "undefined") return;

  clearExisting(hostId);
  host.innerHTML = "";

  const payload = JSON.parse(payloadJson || "{}");
  const nodes = Array.isArray(payload.nodes) ? payload.nodes.map((n) => ({ ...n })) : [];
  const links = Array.isArray(payload.links)
    ? payload.links.map((l) => ({ ...l, source: l.source, target: l.target }))
    : [];

  if (nodes.length === 0) {
    return;
  }

  const width = host.clientWidth || 900;
  const height = host.clientHeight || 600;

  renderLegend(host, payload.queueMode || "server");

  const svg = d3
    .select(host)
    .append("svg")
    .attr("width", width)
    .attr("height", height)
    .attr("class", "graph-svg");

  const canvas = svg.append("g");

  const zoom = d3.zoom().scaleExtent([0.15, 8]).on("zoom", (event) => {
    canvas.attr("transform", event.transform);
  });
  svg.call(zoom);

  const radius = (d) => Math.max(4, Math.min(30, 4 + Math.sqrt(Number(d.size || 1)) * 2.4));

  const baseLinkLayer = canvas.append("g").attr("class", "graph-links-base");
  const candidateLinkLayer = canvas.append("g").attr("class", "graph-links-candidate");
  const nodeLayer = canvas.append("g").attr("class", "graph-nodes");
  const seedLayer = canvas.append("g").attr("class", "graph-seeds");
  const workerLayer = canvas.append("g").attr("class", "graph-workers");

  const baseLink = baseLinkLayer
    .selectAll("line")
    .data(links)
    .join("line")
    .attr("stroke", "#95a5b2")
    .attr("stroke-opacity", 0.25)
    .attr("stroke-width", 1);

  const nodeSelection = nodeLayer
    .selectAll("circle")
    .data(nodes)
    .join("circle")
    .attr("r", radius)
    .attr("fill", (d) => makeNodeColor(d))
    .attr("stroke", "#ffffff")
    .attr("stroke-width", 1.2)
    .call(
      d3
        .drag()
        .on("start", (event, d) => {
          if (!event.active) simulation.alphaTarget(0.25).restart();
          d.fx = d.x;
          d.fy = d.y;
        })
        .on("drag", (event, d) => {
          d.fx = event.x;
          d.fy = event.y;
        })
        .on("end", (event, d) => {
          if (!event.active) simulation.alphaTarget(0);
          d.fx = null;
          d.fy = null;
        })
    );

  const tooltip = d3
    .select(host)
    .append("div")
    .attr("class", "graph-tooltip")
    .style("opacity", 0);

  const linkBySource = new Map();
  const nodeById = new Map(nodes.map((node) => [Number(node.id), node]));
  const rawLinks = links.map((link) => ({ source: Number(link.source), target: Number(link.target) }));

  for (const link of rawLinks) {
    const entries = linkBySource.get(link.source) || [];
    entries.push(link);
    linkBySource.set(link.source, entries);
  }

  const seedNodes = pickSeedNodes(nodes);
  const seedIdSet = new Set(seedNodes.map((node) => Number(node.id)));
  for (const node of nodes) {
    node._visited = false;
    node._isCandidate = false;
    node._candidatePriority = 0;
    node._seed = seedIdSet.has(Number(node.id));
    node._score = 0;
    node._category = "generic";
  }

  const seedSelection = seedLayer
    .selectAll("circle")
    .data(seedNodes)
    .join("circle")
    .attr("r", (d) => radius(d) + 4)
    .attr("fill", "none")
    .attr("stroke", "#f2b84b")
    .attr("stroke-width", 2.2)
    .attr("stroke-dasharray", "3 2");

  const candidateQueue = new Map();
  const candidateEdgePriority = new Map();

  function enqueueCandidatesFromNode(sourceNode) {
    const outgoing = linkBySource.get(Number(sourceNode.id)) || [];
    for (const edge of outgoing) {
      const target = nodeById.get(Number(edge.target));
      if (!target) continue;
      if (target._visited) continue;

      const classification = classifyNode(target.url);
      const backlinkBoost = Math.min(1.2, Math.log10((target.size || 1) + 1) / 2);
      const priority = clamp(Math.round(4 + classification.score * 8 + backlinkBoost * 4), 1, 15);

      const currentPriority = candidateQueue.get(target.id) || 0;
      if (priority > currentPriority) {
        candidateQueue.set(target.id, priority);
        target._isCandidate = true;
        target._candidatePriority = priority;
      }

      const edgePriority = Math.max(priority, candidateEdgePriority.get(edgeKey(edge.source, edge.target)) || 0);
      candidateEdgePriority.set(edgeKey(edge.source, edge.target), edgePriority);
    }
  }

  for (const seedNode of seedNodes) {
    enqueueCandidatesFromNode(seedNode);
  }

  const workerCount = clamp(Math.max(2, Math.floor(nodes.length / 120) + 1), 2, 6);
  const workers = Array.from({ length: workerCount }).map((_, index) => {
    const seed = seedNodes[index % seedNodes.length];
    return {
      id: index + 1,
      status: "Active",
      currentNodeId: Number(seed.id),
      targetNodeId: null,
      moving: false,
      x: seed.x || width / 2,
      y: seed.y || height / 2,
    };
  });

  const workerGroups = workerLayer
    .selectAll("g")
    .data(workers)
    .join("g")
    .attr("class", "graph-worker");

  workerGroups
    .append("circle")
    .attr("r", 9)
    .attr("fill", "#1f3a5f")
    .attr("stroke", "#ffffff")
    .attr("stroke-width", 1.5);

  workerGroups
    .append("text")
    .attr("text-anchor", "middle")
    .attr("dominant-baseline", "central")
    .attr("font-size", 9)
    .attr("font-weight", "700")
    .attr("fill", "#ffffff")
    .text((d) => `W${d.id}`);

  function updateNodeVisuals() {
    nodeSelection
      .attr("fill", (d) => makeNodeColor(d))
      .attr("stroke", (d) => {
        const occupied = workers.some((worker) => worker.currentNodeId === Number(d.id));
        if (occupied) return "#1f3a5f";
        if (d._seed) return "#d58a00";
        return "#ffffff";
      })
      .attr("stroke-width", (d) => {
        const occupied = workers.some((worker) => worker.currentNodeId === Number(d.id));
        if (occupied) return 2.6;
        return d._seed ? 2.2 : 1.2;
      });
  }

  function updateCandidateLinks() {
    const candidateEdges = rawLinks
      .map((edge) => {
        const priority = candidateEdgePriority.get(edgeKey(edge.source, edge.target)) || 0;
        if (priority <= 0) return null;

        const source = nodeById.get(edge.source);
        const target = nodeById.get(edge.target);
        if (!source || !target) return null;

        return {
          source,
          target,
          priority,
        };
      })
      .filter((item) => item !== null);

    const selection = candidateLinkLayer
      .selectAll("line")
      .data(candidateEdges, (d) => edgeKey(d.source.id, d.target.id));

    selection.exit().remove();

    selection
      .enter()
      .append("line")
      .merge(selection)
      .attr("stroke", "#8c96a0")
      .attr("stroke-opacity", (d) => clamp(0.2 + d.priority / 22, 0.2, 0.8))
      .attr("stroke-width", (d) => clamp(1 + d.priority / 5, 1, 4))
      .attr("stroke-dasharray", "4 3");
  }

  function updateWorkerVisuals() {
    workerGroups.attr("transform", (worker) => `translate(${worker.x + 12}, ${worker.y - 12})`);
  }

  function chooseBestCandidate(excludedNodeIds) {
    let bestId = null;
    let bestPriority = -1;

    for (const [nodeId, priority] of candidateQueue.entries()) {
      if (excludedNodeIds.has(Number(nodeId))) continue;
      if (priority > bestPriority) {
        bestPriority = priority;
        bestId = Number(nodeId);
      }
    }

    return bestId;
  }

  function completeVisit(worker, destinationNode) {
    worker.currentNodeId = Number(destinationNode.id);
    worker.targetNodeId = null;
    worker.moving = false;

    destinationNode._isCandidate = false;
    destinationNode._candidatePriority = 0;
    candidateQueue.delete(Number(destinationNode.id));

    const classified = classifyNode(destinationNode.url);
    destinationNode._visited = true;
    destinationNode._category = classified.category;
    destinationNode._score = classified.score;

    enqueueCandidatesFromNode(destinationNode);

    for (const [key, _priority] of candidateEdgePriority.entries()) {
      const [, targetRaw] = key.split("->");
      const targetId = Number(targetRaw);
      const targetNode = nodeById.get(targetId);
      if (!targetNode || !targetNode._isCandidate) {
        candidateEdgePriority.delete(key);
      }
    }

    updateCandidateLinks();
    updateNodeVisuals();
  }

  function stepWorkers() {
    const occupied = new Set(workers.map((worker) => Number(worker.currentNodeId)));

    for (const worker of workers) {
      if (worker.moving) continue;

      const destinationId = chooseBestCandidate(occupied);
      if (!destinationId) {
        worker.status = "Idle";
        continue;
      }

      const destination = nodeById.get(destinationId);
      const current = nodeById.get(Number(worker.currentNodeId));
      if (!destination || !current) continue;

      worker.status = "Active";
      worker.targetNodeId = destinationId;
      worker.moving = true;

      occupied.add(destinationId);
      candidateQueue.delete(destinationId);

      const startX = current.x || worker.x;
      const startY = current.y || worker.y;
      const endX = destination.x || startX;
      const endY = destination.y || startY;

      d3.select(worker)
        .transition()
        .duration(1250)
        .ease(d3.easeCubicInOut)
        .tween("worker-move", () => {
          return (t) => {
            worker.x = startX + (endX - startX) * t;
            worker.y = startY + (endY - startY) * t;
            updateWorkerVisuals();
          };
        })
        .on("end", () => completeVisit(worker, destination));
    }

    updateNodeVisuals();
  }

  nodeSelection
    .append("title")
    .text((d) => `${d.url}\nBacklinks: ${d.size || 0}`);

  nodeSelection
    .on("mouseover", (_, d) => {
      tooltip
        .style("opacity", 1)
        .html(
          `<div><strong>${sanitize(d.domain || "unknown")}</strong></div>` +
            `<div>${sanitize(d.url)}</div>` +
            `<div>backlinks: ${sanitize(d.size || 0)}</div>` +
            `<div>score: ${sanitize((d._score || 0).toFixed(2))}</div>` +
            `<div>state: ${d._isCandidate ? "candidate" : d._visited ? "scanned" : d._seed ? "seed" : "discovered"}</div>`
        );
    })
    .on("mousemove", (event) => {
      tooltip.style("left", `${event.offsetX + 12}px`).style("top", `${event.offsetY + 12}px`);
    })
    .on("mouseout", () => tooltip.style("opacity", 0));

  const simulation = d3
    .forceSimulation(nodes)
    .force("link", d3.forceLink(links).id((d) => d.id).distance(62).strength(0.2))
    .force("charge", d3.forceManyBody().strength(-140))
    .force("center", d3.forceCenter(width / 2, height / 2))
    .force("collision", d3.forceCollide().radius((d) => radius(d) + 4));

  simulation.on("tick", () => {
    baseLink
      .attr("x1", (d) => d.source.x)
      .attr("y1", (d) => d.source.y)
      .attr("x2", (d) => d.target.x)
      .attr("y2", (d) => d.target.y);

    candidateLinkLayer
      .selectAll("line")
      .attr("x1", (d) => d.source.x)
      .attr("y1", (d) => d.source.y)
      .attr("x2", (d) => d.target.x)
      .attr("y2", (d) => d.target.y);

    nodeSelection.attr("cx", (d) => d.x).attr("cy", (d) => d.y);
    seedSelection.attr("cx", (d) => d.x).attr("cy", (d) => d.y);

    for (const worker of workers) {
      if (!worker.moving) {
        const current = nodeById.get(Number(worker.currentNodeId));
        if (current) {
          worker.x = current.x || worker.x;
          worker.y = current.y || worker.y;
        }
      }
    }

    updateWorkerVisuals();
  });

  updateCandidateLinks();
  updateNodeVisuals();
  updateWorkerVisuals();

  const workerTimer = d3.interval(() => {
    stepWorkers();
  }, 1600);

  state.set(hostId, {
    svg,
    node: nodeSelection,
    zoom,
    width,
    height,
    nodes,
    simulation,
    workerTimer,
  });
}

export function focusNode(hostId, term) {
  const current = state.get(hostId);
  if (!current || !term) return;

  const matchTerm = term.toLowerCase();
  const match = current.nodes.find((n) => {
    return (n.url || "").toLowerCase().includes(matchTerm) || (n.domain || "").toLowerCase().includes(matchTerm);
  });

  current.node
    .attr("stroke", (d) => (match && d.id === match.id ? "#ff7f0e" : d._seed ? "#d58a00" : "#ffffff"))
    .attr("stroke-width", (d) => (match && d.id === match.id ? 3.6 : d._seed ? 2.2 : 1.2));

  if (!match) return;

  const transform = d3.zoomIdentity
    .translate(current.width / 2 - (match.x || 0) * 1.5, current.height / 2 - (match.y || 0) * 1.5)
    .scale(1.5);

  current.svg
    .transition()
    .duration(450)
    .call(current.zoom.transform, transform);
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

  const a = document.createElement("a");
  a.href = url;
  a.download = filename || "crawler-graph.svg";
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);

  URL.revokeObjectURL(url);
}
