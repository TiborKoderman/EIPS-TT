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

export function renderGraph(hostId, payloadJson) {
  const host = document.getElementById(hostId);
  if (!host || typeof d3 === "undefined") return;

  host.innerHTML = "";
  const payload = JSON.parse(payloadJson || "{}");
  const nodes = Array.isArray(payload.nodes) ? payload.nodes.map((n) => ({ ...n })) : [];
  const links = Array.isArray(payload.links) ? payload.links.map((l) => ({ ...l })) : [];

  const width = host.clientWidth || 900;
  const height = host.clientHeight || 600;

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

  const link = canvas
    .append("g")
    .attr("stroke", "#94a6b8")
    .attr("stroke-opacity", 0.45)
    .selectAll("line")
    .data(links)
    .join("line")
    .attr("stroke-width", 1.1);

  const radius = (d) => Math.max(4, Math.min(26, 4 + Math.sqrt(Number(d.size || 1)) * 2.8));
  const color = d3.scaleOrdinal(d3.schemeTableau10);

  const node = canvas
    .append("g")
    .attr("stroke", "#fff")
    .attr("stroke-width", 1.2)
    .selectAll("circle")
    .data(nodes)
    .join("circle")
    .attr("r", radius)
    .attr("fill", (d) => color(d.domain || d.pageType || "domain"))
    .call(
      d3
        .drag()
        .on("start", (event, d) => {
          if (!event.active) simulation.alphaTarget(0.3).restart();
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

  node.append("title").text((d) => `${d.url}\nDomain: ${d.domain}\nIn-links: ${d.size || 0}`);

  const tooltip = d3
    .select(host)
    .append("div")
    .attr("class", "graph-tooltip")
    .style("opacity", 0);

  node
    .on("mouseover", (_, d) => {
      tooltip
        .style("opacity", 1)
        .html(`<div><strong>${sanitize(d.domain || "unknown")}</strong></div><div>${sanitize(d.url)}</div>`);
    })
    .on("mousemove", (event) => {
      tooltip.style("left", `${event.offsetX + 12}px`).style("top", `${event.offsetY + 12}px`);
    })
    .on("mouseout", () => tooltip.style("opacity", 0));

  const simulation = d3
    .forceSimulation(nodes)
    .force("link", d3.forceLink(links).id((d) => d.id).distance(55).strength(0.18))
    .force("charge", d3.forceManyBody().strength(-120))
    .force("center", d3.forceCenter(width / 2, height / 2))
    .force("collision", d3.forceCollide().radius((d) => radius(d) + 2));

  simulation.on("tick", () => {
    link
      .attr("x1", (d) => d.source.x)
      .attr("y1", (d) => d.source.y)
      .attr("x2", (d) => d.target.x)
      .attr("y2", (d) => d.target.y);

    node.attr("cx", (d) => d.x).attr("cy", (d) => d.y);
  });

  state.set(hostId, { svg, node, zoom, width, height, nodes });
}

export function focusNode(hostId, term) {
  const current = state.get(hostId);
  if (!current || !term) return;

  const matchTerm = term.toLowerCase();
  const match = current.nodes.find((n) => {
    return (n.url || "").toLowerCase().includes(matchTerm) || (n.domain || "").toLowerCase().includes(matchTerm);
  });

  current.node
    .attr("stroke", (d) => (match && d.id === match.id ? "#ff7f0e" : "#ffffff"))
    .attr("stroke-width", (d) => (match && d.id === match.id ? 3.6 : 1.2));

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
