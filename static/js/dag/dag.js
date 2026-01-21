import * as d3base from "https://cdn.skypack.dev/d3@7.8.4"
import * as d3dag from "https://cdn.skypack.dev/d3-dag@1.1.0"

function fetchData() {
    const currentLocation = decodeURIComponent(location.href)
    const pageRegex = /sampletree/
    const pageMatch = currentLocation.match(pageRegex)
    let sampleRegex;
    if (pageMatch !== null && pageMatch.length > 0) {
      sampleRegex = /=(.*[\w])/
    } else {
      sampleRegex = /=(.*[\d])/
    }
    const sample = currentLocation.match(sampleRegex).at(-1)
    const data = d3.json("/nextseek_api/sample-tree/" + sample + "/tree")
    return data;
}

const d3 = Object.assign({}, d3base, d3dag)

const nodeRadius = 10
const gap = nodeRadius * 100 
const circleRadius = 40
const hMultiplier = 0.095
const data = await fetchData();
const nodesData = data.nodes
const relationships = data.rels
document.getElementById("loading-spinner").remove();
const dag = d3.graphStratify()(nodesData);

const layout = d3.sugiyama()
                 .layering(d3.layeringLongestPath())
                 .decross(d3.decrossTwoLayer())
                 .coord(d3.coordSimplex())
                 .nodeSize([nodeRadius, nodeRadius])
                 .gap([gap, gap])
                 .tweaks([d3.tweakFlip()])

const { width, height } = layout(dag);

const svg = d3.select("#svg")
              .attr("width", "100%")
              .attr("height", "100%")
              .attr("viewBox", [0, 0, 800, 600]) 

const graphGroup = d3.select("#graphGroup")

const tooltip = d3.select("body").append("div")
    .style("position", "absolute")
    .style("background", "white")
    .style("padding", "6px")
    .style("border", "1px solid black")
    .style("border-radius", "4px")
    .style("visibility", "hidden")
    .style("font-size", "14px");

function mouseOverNode(e, d) {
        d3.select(this).select("circle")
          .attr("fill", "white")

        tooltip.html(`<p>ID: ${d.data.id}</p>
                      <p>UUID: ${d.data.uuid}</p>
                      <p>TYPE: ${d.data.sample_type}</p>`)
               .style("visibility", "visible")
               .style("left", (e.pageX + 10) + "px")
               .style("top", (e.pageY + 10) + "px")
}

function mouseOutNode(e, d) {
        d3.select(this).select("circle")
          .attr("fill", d.data.color) 

        tooltip.style("visibility", "hidden")
}

function clickNode(e, d) {
    const nodeId = d.data.id;
    const sampleId = d.data.id;
    window.open("https://nextseek-dev.mit.edu/seek/sample/id=" + sampleId)
}

const nodes = svg.select("#nodes")
                 .selectAll("g")
                 .data(dag.nodes())
                 .enter()
                 .append("g")
                 .attr("class", "node")
                 .attr("id", "retrieval_uids")
                 .attr("transform", ({ x, y }) => `translate(${x}, ${y * hMultiplier})`)
                 .on("mouseover", mouseOverNode)
                 .on("mouseout", mouseOutNode)
                 .on("click", clickNode)

nodes.append("circle")
     .attr("r", circleRadius)
     .attr("stroke-width", 1)
     .attr("stroke", "black")
     .attr("fill", (d) => {
                 const color = d.data.color;
                 return color == undefined ? "black" : color
              });


const line = d3.line().curve(d3.curveMonotoneX);

function getRelationship(child, parent) {
  return relationships.filter(rel => {
    if (rel.child_id == child & rel.parent_id == parent) {
      return rel
    }
  })[0]
}

function relsToHtml(rel) {
  let relStr = ""
  if ('internal_assay_title' in rel) {
    relStr += `<p><strong>Assay:</strong> <a href="https://fairdata-dev.mit.edu/assays/${rel['assay_id']}">${rel['internal_assay_title']}</a></p>`
  } else {
    relStr += `<p><strong>Assay:</strong> <a href="https://fairdata-dev.mit.edu/assays/${rel['assay_id']}">${rel['assay_title']}</a></p>`
  }

  if ('protocol_title' in rel) {
    relStr += `<p><strong>Protocol:</strong> <a href="https://fairdata-dev.mit.edu/sops/${rel['protocol_id']}">${rel['protocol_title']}</a></p>`
  }
  
  return relStr
}

function mouseOverLine(e, d) {
    const parentId = d.source.data.id
    const childId = d.target.data.id
    const rels = getRelationship(childId, parentId)
    tooltip.html(relsToHtml(rels))
           .style("visibility", "visible")
           .style("left", (e.pageX + 10) + "px")
           .style("top", (e.pageY + 10) + "px")
}

svg.select("#links")
   .selectAll("path")
   .data(dag.links())
   .enter()
   .append("path")
   .attr("d", ({ points }) => line(points.map(s => { s[1] *= hMultiplier; return s })))
   .attr("fill", "none")
   .attr("stroke-width", 3)
   .attr("stroke", "black")
   .on("mouseover", mouseOverLine)

nodes.append('text')
     .text(({ data }) => data.uuid) 
     .attr('paint-order', 'stroke')
     .attr('stroke', 'white')
     .attr('stroke-width', 4)
     .attr('font-size', '5em')
     .attr('font-weight', 'bold')
     .attr('font-family', 'sans-serif')
     .attr('text-anchor', 'middle')
     .attr('alignment-baseline', 'text-top')
     .attr('fill', 'black');

const zoom = d3.zoom()
                .scaleExtent([0.05, 3]) // Min and max zoom levels
                .on("zoom", function (event) {
                                        graphGroup.attr("transform", event.transform);
                                    });

svg.call(zoom)

const centerGraph = () => {
        const bounds = graphGroup.node().getBBox(); // Get graph size
        const width = bounds.width, height = bounds.height;
        const x = bounds.x + width / 2, y = bounds.y + height / 2;

        const scale = 0.075;
        const translate = [300 - x * scale, 200 - y * scale]; // Center in SVG

        svg.call(zoom.transform, d3.zoomIdentity.translate(...translate).scale(scale));
};

centerGraph()
