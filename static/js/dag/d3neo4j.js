import * as d3 from "https://cdn.jsdelivr.net/npm/d3@7.9.0/+esm";

function d3Neo4J(_selector, _options){
	var container, graph, info, node, nodes, relationship, relationshipOutline, relationshipOverlay, relationshipText, relationships, selector, simulation, svg, svgNodes, svgRelationships, svgScale, svgTranslate,
	classes2colors = {},
	justLoaded = false,
	numClasses = 0,
	options = {
		arrowSize: 4,
		colors: colors(),
		highlight: undefined,
		infoPanel: true,
		minCollision: undefined,
		neo4jData: undefined,
		neo4jDataUrl: undefined,
		nodeOutlineFillColor: undefined,
		nodeRadius: 25,
		relationshipColor: "#a5abb6",
		zoomFit: false,
		showNodeLabel: false,
		nodeLabelFunction: undefined,
	},
	VERSION = "1.0.0";

function appendGraph(container){
	svg = container.append("svg")
					.attr("width", "100%")
					.attr("height", "100%")
					.attr("class", "d3Neo4J-graph")
					.call(d3.zoom().on("zoom", function(event){
						var scale = event.transform.k,
							translate = [event.transform.x, event.transform.y];

						if(svgTranslate){
							translate[0] += svgTranslate[0];
							translate[1] += svgTranslate[1];
						}

						if(svgScale){
							scale *= svgScale;
						}

						svg.attr("transform", "translate(" + translate[0] + ", " + translate[1] + ") scale(" + scale + ")");
					}))
					.on("dblclick.zoom", null)
					.append("g")
					.attr("width", "100%")
					.attr("height", "100%");

	svgRelationships = svg.append("g")
							.attr("class", "relationships");

	svgNodes = svg.append("g")
					.attr("class", "nodes");
}

function appendInfoPanel(container){
	return container.append("div")
					.attr("class", "d3Neo4J-info");
}

function appendInfoElement(cls, isNode, property, value){
	var elem = info.append("a");

	elem.attr("href", "#")
		.attr("class", cls)
		.html("<strong>" + property + "</strong>" + (value ? (": " + value) : ""));

	if(!value){
		elem.style("background-color", function(d){
				return options.nodeOutlineFillColor ? options.nodeOutlineFillColor : (isNode ? class2color(property) : defaultColor());
			})
			.style("border-color", function(d){
				return options.nodeOutlineFillColor ? class2darkenColor(options.nodeOutlineFillColor) : (isNode ? class2darkenColor(property) : defaultDarkenColor());
			})
			.style("color", function(d){
				return options.nodeOutlineFillColor ? class2darkenColor(options.nodeOutlineFillColor) : "#fff";
			});
	}
}

function appendInfoElementClass(cls, node){
	appendInfoElement(cls, true, node);
}

function appendInfoElementProperty(cls, property, value){
	appendInfoElement(cls, false, property, value);
}

function appendInfoElementRelationship(cls, relationship){
	appendInfoElement(cls, false, relationship);
}

function appendNode(){
	return node.enter()
				.append("g")
				.attr("class", function(d){
					var highlight, i,
						classes = "node",
						label = d.labels[0];

					if(options.highlight){
						for (i = 0; i < options.highlight.length; i++){
							highlight = options.highlight[i];

							if(d.id === highlight.id){
								classes += " node-highlighted";
								break;
							}
						}
					}

					return classes;
				})
				.on("click", function(d){
					d.fx = d.fy = null;

					if(typeof options.onNodeClick === "function"){
						options.onNodeClick(d);
					}
				})
				.on("dblclick", function(d){
					stickNode(d);

					if(typeof options.onNodeDoubleClick === "function"){
						options.onNodeDoubleClick(d);
					}
				})
				.on("mouseenter", function(d){
					if(info){
						updateInfo(d);
					}

					if(typeof options.onNodeMouseEnter === "function"){
						options.onNodeMouseEnter(d);
					}
				})
				.on("mouseleave", function(d){
					if(info){
						clearInfo(d);
					}

					if(typeof options.onNodeMouseLeave === "function"){
						options.onNodeMouseLeave(d);
					}
				})
				.call(d3.drag()
						.on("start", dragStarted)
						.on("drag", dragged)
						.on("end", dragEnded));
}

function appendNodeLabelToNode(n){

	let displayFnc = (d) => d.name;

	if(options.nodeLabelFunction) displayFnc = options.nodeLabelFunction;


	console.log("run on")

	return n.append("text")
			.classed("circleText", true)
			.attr("dy", options.nodeRadius * 2)
			// .attr("dx", (d)=> {
			//     let display = displayFnc(d);
			//     let radius = options.nodeRadius;

			//     let textWidth = display.length * 10;

			//     return -textWidth / 2;

			// })
			.style("text-anchor", "middle")
			.text(displayFnc);
}

function appendNodeToGraph(){
	var n = appendNode();

	appendRingToNode(n);
	appendOutlineToNode(n);


	if(options.showNodeLabel){
		appendNodeLabelToNode(n);
	}

	return n;
}

function appendOutlineToNode(node){
	return node.append("circle")
				.attr("class", "outline")
				.attr("r", options.nodeRadius)
				.style("fill", function(d){
					return options.nodeOutlineFillColor ? options.nodeOutlineFillColor : class2color(d.labels[0]);
				})
				.style("stroke", function(d){
					return options.nodeOutlineFillColor ? class2darkenColor(options.nodeOutlineFillColor) : class2darkenColor(d.labels[0]);
				})
				.append("title").text(function(d){
					return toString(d);
				});
}

function appendRingToNode(node){
	return node.append("circle")
				.attr("class", "ring")
				.attr("r", options.nodeRadius * 1.16)
				.append("title").text(function(d){
					return toString(d);
				});
}


function appendRandomDataToNode(d, maxNodesToGenerate){
	var data = randomD3Data(d, maxNodesToGenerate);
	updateWithNeo4jData(data);
}

function appendRelationship(){
	return relationship.enter()
						.append("g")
						.attr("class", "relationship")
						.on("dblclick", function(d){
							if(typeof options.onRelationshipDoubleClick === "function"){
								options.onRelationshipDoubleClick(d);
							}
						})
						.on("mouseenter", function(d){
							if(info){
								updateInfo(d);
							}
						});
}

function appendOutlineToRelationship(r){
	return r.append("path")
			.attr("class", "outline")
			.attr("fill", "#a5abb6")
			.attr("stroke", "none");
}

function appendOverlayToRelationship(r){
	return r.append("path")
			.attr("class", "overlay");
}

function appendTextToRelationship(r){
	return r.append("text")
			.attr("class", "text")
			.attr("fill", "#000000")
			.attr("font-size", "8px")
			.attr("pointer-events", "none")
			.attr("text-anchor", "middle")
			.text(function(d){
				return d.type;
			});
}

function appendRelationshipToGraph(){
	var relationship = appendRelationship(),
		text = appendTextToRelationship(relationship),
		outline = appendOutlineToRelationship(relationship),
		overlay = appendOverlayToRelationship(relationship);


	return {
		outline: outline,
		overlay: overlay,
		relationship: relationship,
		text: text
	};
}

function class2color(cls){
	var color = classes2colors[cls];

	if(!color){
//            color = options.colors[Math.min(numClasses, options.colors.length - 1)];
		color = options.colors[numClasses % options.colors.length];
		classes2colors[cls] = color;
		numClasses++;
	}

	return color;
}

function class2darkenColor(cls){
	return d3.rgb(class2color(cls)).darker(1);
}

function clearInfo(){
	info.html("");
}

function color(){
	return options.colors[options.colors.length * Math.random() << 0];
}

function colors(){
	// d3.schemeCategory10,
	// d3.schemeCategory20,
	return [
		"#68bdf6", // light blue
		"#6dce9e", // green #1
		"#faafc2", // light pink
		"#f2baf6", // purple
		"#ff928c", // light red
		"#fcea7e", // light yellow
		"#ffc766", // light orange
		"#405f9e", // navy blue
		"#a5abb6", // dark gray
		"#78cecb", // green #2,
		"#b88cbb", // dark purple
		"#ced2d9", // light gray
		"#e84646", // dark red
		"#fa5f86", // dark pink
		"#ffab1a", // dark orange
		"#fcda19", // dark yellow
		"#797b80", // black
		"#c9d96f", // pistacchio
		"#47991f", // green #3
		"#70edee", // turquoise
		"#ff75ea"  // pink
	];
}

function contains(array, id){
	var filter = array.filter(function(elem){
		return elem.id === id;
	});

	return filter.length > 0;
}

function defaultColor(){
	return options.relationshipColor;
}

function defaultDarkenColor(){
	return d3.rgb(options.colors[options.colors.length - 1]).darker(1);
}

function dragEnded(event, d){
	if(!event.active){
		simulation.alphaTarget(0);
	}

	if(typeof options.onNodeDragEnd === "function"){
		options.onNodeDragEnd(d);
	}
}

function dragged(d){
	stickNode(d);
}

function dragStarted(event, d){

	if(!event.active){
		simulation.alphaTarget(0.3).restart();
	}

	d.fx = d.x;
	d.fy = d.y;

	if(typeof options.onNodeDragStart === "function"){
		options.onNodeDragStart(d);
	}
}

function extend(obj1, obj2){
	var obj = {};

	merge(obj, obj1);
	merge(obj, obj2);

	return obj;
}

function init(_selector, _options){

	merge(options, _options);

	if(!options.minCollision){
		options.minCollision = options.nodeRadius * 2;
	}

	selector = _selector;

	container = d3.select(selector);

	container.attr("class", "d3Neo4J")
				.html("");

	if(options.infoPanel){
		info = appendInfoPanel(container);
	}

	appendGraph(container);

	simulation = initSimulation();

	if(options.neo4jData){
		loadNeo4jData(options.neo4jData);
	} else if(options.neo4jDataUrl){
		loadNeo4jDataFromUrl(options.neo4jDataUrl);
	} else {
		console.error("Error: both neo4jData and neo4jDataUrl are empty!");
	}
}

function initSimulation(){
	var simulation = d3.forceSimulation()
//                           .velocityDecay(0.8)
//                           .force("x", d3.force().strength(0.002))
//                           .force("y", d3.force().strength(0.002))
						.force("collide", d3.forceCollide().radius(function(d){
							return options.minCollision;
						}).iterations(2))
						.force("charge", d3.forceManyBody())
						.force("link", d3.forceLink().id(function(d){
							return d.id;
						}))
						.force("center", d3.forceCenter(svg.node().parentElement.parentElement.clientWidth / 2, svg.node().parentElement.parentElement.clientHeight / 2))
						.on("tick", function(){
							tick();
						})
						.on("end", function(){
							if(options.zoomFit && !justLoaded){
								justLoaded = true;
								zoomFit(2);
							}
						});

	return simulation;
}

function loadNeo4jData(){
	nodes = [];
	relationships = [];

	updateWithNeo4jData(options.neo4jData);
}

function loadNeo4jDataFromUrl(neo4jDataUrl){
	nodes = [];
	relationships = [];

	d3.json(neo4jDataUrl, function(error, data){
		if(error){
			throw error;
		}

		updateWithNeo4jData(data);
	});
}

function merge(target, source){
	Object.keys(source).forEach(function(property){
		target[property] = source[property];
	});
}

function neo4jDataToD3Data(data){
	var graph = {
		nodes: [],
		relationships: []
	};

	data.results.forEach(function(result){
		result.data.forEach(function(data){
			data.graph.nodes.forEach(function(node){
				if(!contains(graph.nodes, node.id)){
					graph.nodes.push(node);
				}
			});

			data.graph.relationships.forEach(function(relationship){
				relationship.source = relationship.startNode;
				relationship.target = relationship.endNode;
				graph.relationships.push(relationship);
			});

			data.graph.relationships.sort(function(a, b){
				if(a.source > b.source){
					return 1;
				} else if(a.source < b.source){
					return -1;
				} else {
					if(a.target > b.target){
						return 1;
					}

					if(a.target < b.target){
						return -1;
					} else {
						return 0;
					}
				}
			});

			for (var i = 0; i < data.graph.relationships.length; i++){
				if(i !== 0 && data.graph.relationships[i].source === data.graph.relationships[i-1].source && data.graph.relationships[i].target === data.graph.relationships[i-1].target){
					data.graph.relationships[i].linknum = data.graph.relationships[i - 1].linknum + 1;
				} else {
					data.graph.relationships[i].linknum = 1;
				}
			}
		});
	});

	return graph;
}

function randomD3Data(d, maxNodesToGenerate){
	var data = {
			nodes: [],
			relationships: []
		},
		i,
		label,
		node,
		numNodes = (maxNodesToGenerate * Math.random() << 0) + 1,
		relationship,
		s = size();

	for (i = 0; i < numNodes; i++){
		label = randomLabel();

		node = {
			id: s.nodes + 1 + i,
			labels: [label],
			properties: {
				random: label
			},
			x: d.x,
			y: d.y
		};

		data.nodes[data.nodes.length] = node;

		relationship = {
			id: s.relationships + 1 + i,
			type: label.toUpperCase(),
			startNode: d.id,
			endNode: s.nodes + 1 + i,
			properties: {
				from: Date.now()
			},
			source: d.id,
			target: s.nodes + 1 + i,
			linknum: s.relationships + 1 + i
		};

		data.relationships[data.relationships.length] = relationship;
	}

	return data;
}

function rotate(cx, cy, x, y, angle){
	var radians = (Math.PI / 180) * angle,
		cos = Math.cos(radians),
		sin = Math.sin(radians),
		nx = (cos * (x - cx)) + (sin * (y - cy)) + cx,
		ny = (cos * (y - cy)) - (sin * (x - cx)) + cy;

	return { x: nx, y: ny };
}

function rotatePoint(c, p, angle){
	return rotate(c.x, c.y, p.x, p.y, angle);
}

function rotation(source, target){
	return Math.atan2(target.y - source.y, target.x - source.x) * 180 / Math.PI;
}

function size(){
	return {
		nodes: nodes.length,
		relationships: relationships.length
	};
}

function stickNode(event){
	if(event){
		event.subject.fx = event.x;
		event.subject.fy = event.y;
	}
}

function tick(){
	tickNodes();
	tickRelationships();
}

function tickNodes(){
	if(node){
		node.attr("transform", function(d){
			return "translate(" + d.x + ", " + d.y + ")";
		});
	}
}

function tickRelationships(){
	if(relationship){
		relationship.attr("transform", function(d){
			var angle = rotation(d.source, d.target);
			return "translate(" + d.source.x + ", " + d.source.y + ") rotate(" + angle + ")";
		});

		tickRelationshipsTexts();
		tickRelationshipsOutlines();
		tickRelationshipsOverlays();
	}
}

function tickRelationshipsOutlines(){
	relationship.each(function(relationship){
		var rel = d3.select(this),
			outline = rel.select(".outline"),
			text = rel.select(".text"),
			bbox = text.node().getBBox(),
			padding = 3;

		outline.attr("d", function(d){
			var center = { x: 0, y: 0 },
				angle = rotation(d.source, d.target),
				textBoundingBox = text.node().getBBox(),
				textPadding = 5,
				u = unitaryVector(d.source, d.target),
				textMargin = { x: (d.target.x - d.source.x - (textBoundingBox.width + textPadding) * u.x) * 0.5, y: (d.target.y - d.source.y - (textBoundingBox.width + textPadding) * u.y) * 0.5 },
				n = unitaryNormalVector(d.source, d.target),
				rotatedPointA1 = rotatePoint(center, { x: 0 + (options.nodeRadius + 1) * u.x - n.x, y: 0 + (options.nodeRadius + 1) * u.y - n.y }, angle),
				rotatedPointB1 = rotatePoint(center, { x: textMargin.x - n.x, y: textMargin.y - n.y }, angle),
				rotatedPointC1 = rotatePoint(center, { x: textMargin.x, y: textMargin.y }, angle),
				rotatedPointD1 = rotatePoint(center, { x: 0 + (options.nodeRadius + 1) * u.x, y: 0 + (options.nodeRadius + 1) * u.y }, angle),
				rotatedPointA2 = rotatePoint(center, { x: d.target.x - d.source.x - textMargin.x - n.x, y: d.target.y - d.source.y - textMargin.y - n.y }, angle),
				rotatedPointB2 = rotatePoint(center, { x: d.target.x - d.source.x - (options.nodeRadius + 1) * u.x - n.x - u.x * options.arrowSize, y: d.target.y - d.source.y - (options.nodeRadius + 1) * u.y - n.y - u.y * options.arrowSize }, angle),
				rotatedPointC2 = rotatePoint(center, { x: d.target.x - d.source.x - (options.nodeRadius + 1) * u.x - n.x + (n.x - u.x) * options.arrowSize, y: d.target.y - d.source.y - (options.nodeRadius + 1) * u.y - n.y + (n.y - u.y) * options.arrowSize }, angle),
				rotatedPointD2 = rotatePoint(center, { x: d.target.x - d.source.x - (options.nodeRadius + 1) * u.x, y: d.target.y - d.source.y - (options.nodeRadius + 1) * u.y }, angle),
				rotatedPointE2 = rotatePoint(center, { x: d.target.x - d.source.x - (options.nodeRadius + 1) * u.x + (- n.x - u.x) * options.arrowSize, y: d.target.y - d.source.y - (options.nodeRadius + 1) * u.y + (- n.y - u.y) * options.arrowSize }, angle),
				rotatedPointF2 = rotatePoint(center, { x: d.target.x - d.source.x - (options.nodeRadius + 1) * u.x - u.x * options.arrowSize, y: d.target.y - d.source.y - (options.nodeRadius + 1) * u.y - u.y * options.arrowSize }, angle),
				rotatedPointG2 = rotatePoint(center, { x: d.target.x - d.source.x - textMargin.x, y: d.target.y - d.source.y - textMargin.y }, angle);

			return "M " + rotatedPointA1.x + " " + rotatedPointA1.y +
					" L " + rotatedPointB1.x + " " + rotatedPointB1.y +
					" L " + rotatedPointC1.x + " " + rotatedPointC1.y +
					" L " + rotatedPointD1.x + " " + rotatedPointD1.y +
					" Z M " + rotatedPointA2.x + " " + rotatedPointA2.y +
					" L " + rotatedPointB2.x + " " + rotatedPointB2.y +
					" L " + rotatedPointC2.x + " " + rotatedPointC2.y +
					" L " + rotatedPointD2.x + " " + rotatedPointD2.y +
					" L " + rotatedPointE2.x + " " + rotatedPointE2.y +
					" L " + rotatedPointF2.x + " " + rotatedPointF2.y +
					" L " + rotatedPointG2.x + " " + rotatedPointG2.y +
					" Z";
		});
	});
}

function tickRelationshipsOverlays(){
	relationshipOverlay.attr("d", function(d){
		var center = { x: 0, y: 0 },
			angle = rotation(d.source, d.target),
			n1 = unitaryNormalVector(d.source, d.target),
			n = unitaryNormalVector(d.source, d.target, 50),
			rotatedPointA = rotatePoint(center, { x: 0 - n.x, y: 0 - n.y }, angle),
			rotatedPointB = rotatePoint(center, { x: d.target.x - d.source.x - n.x, y: d.target.y - d.source.y - n.y }, angle),
			rotatedPointC = rotatePoint(center, { x: d.target.x - d.source.x + n.x - n1.x, y: d.target.y - d.source.y + n.y - n1.y }, angle),
			rotatedPointD = rotatePoint(center, { x: 0 + n.x - n1.x, y: 0 + n.y - n1.y }, angle);

		return "M " + rotatedPointA.x + " " + rotatedPointA.y +
				" L " + rotatedPointB.x + " " + rotatedPointB.y +
				" L " + rotatedPointC.x + " " + rotatedPointC.y +
				" L " + rotatedPointD.x + " " + rotatedPointD.y +
				" Z";
	});
}

function tickRelationshipsTexts(){
	relationshipText.attr("transform", function(d){
		var angle = (rotation(d.source, d.target) + 360) % 360,
			mirror = angle > 90 && angle < 270,
			center = { x: 0, y: 0 },
			n = unitaryNormalVector(d.source, d.target),
			nWeight = mirror ? 2 : -3,
			point = { x: (d.target.x - d.source.x) * 0.5 + n.x * nWeight, y: (d.target.y - d.source.y) * 0.5 + n.y * nWeight },
			rotatedPoint = rotatePoint(center, point, angle);

		return "translate(" + rotatedPoint.x + ", " + rotatedPoint.y + ") rotate(" + (mirror ? 180 : 0) + ")";
	});
}

function toString(d){
	var s = d.labels ? d.labels[0] : d.type;

	s += " (<id>: " + d.id;

	Object.keys(d.properties).forEach(function(property){
		s += ", " + property + ": " + JSON.stringify(d.properties[property]);
	});

	s += ")";

	return s;
}

function unitaryNormalVector(source, target, newLength){
	var center = { x: 0, y: 0 },
		vector = unitaryVector(source, target, newLength);

	return rotatePoint(center, vector, 90);
}

function unitaryVector(source, target, newLength){
	var length = Math.sqrt(Math.pow(target.x - source.x, 2) + Math.pow(target.y - source.y, 2)) / Math.sqrt(newLength || 1);

	return {
		x: (target.x - source.x) / length,
		y: (target.y - source.y) / length,
	};
}

function updateWithD3Data(d3Data){
	updateNodesAndRelationships(d3Data.nodes, d3Data.relationships);
}

function updateWithNeo4jData(neo4jData){
	var d3Data = neo4jDataToD3Data(neo4jData);
	updateWithD3Data(d3Data);
}

function updateInfo(d){
	clearInfo();

	if(d.labels){
		appendInfoElementClass("class", d.labels[0]);
	} else {
		appendInfoElementRelationship("class", d.type);
	}

	appendInfoElementProperty("property", "&lt;id&gt;", d.id);

	Object.keys(d.properties).forEach(function(property){
		appendInfoElementProperty("property", property, JSON.stringify(d.properties[property]));
	});
}

function updateNodes(n){
	Array.prototype.push.apply(nodes, n);

	node = svgNodes.selectAll(".node")
					.data(nodes, function(d){ return d.id; });
	var nodeEnter = appendNodeToGraph();
	node = nodeEnter.merge(node);
}

function updateNodesAndRelationships(n, r){
	updateRelationships(r);
	updateNodes(n);

	simulation.nodes(nodes);
	simulation.force("link").links(relationships);
}

function updateRelationships(r){
	Array.prototype.push.apply(relationships, r);

	relationship = svgRelationships.selectAll(".relationship")
									.data(relationships, function(d){ return d.id; });

	var relationshipEnter = appendRelationshipToGraph();

	relationship = relationshipEnter.relationship.merge(relationship);

	relationshipOutline = svg.selectAll(".relationship .outline");
	relationshipOutline = relationshipEnter.outline.merge(relationshipOutline);

	relationshipOverlay = svg.selectAll(".relationship .overlay");
	relationshipOverlay = relationshipEnter.overlay.merge(relationshipOverlay);

	relationshipText = svg.selectAll(".relationship .text");
	relationshipText = relationshipEnter.text.merge(relationshipText);
}

function version(){
	return VERSION;
}

function zoomFit(transitionDuration){
	var bounds = svg.node().getBBox(),
		parent = svg.node().parentElement.parentElement,
		fullWidth = parent.clientWidth,
		fullHeight = parent.clientHeight,
		width = bounds.width,
		height = bounds.height,
		midX = bounds.x + width / 2,
		midY = bounds.y + height / 2;

	if(width === 0 || height === 0){
		return; // nothing to fit
	}

	svgScale = 0.85 / Math.max(width / fullWidth, height / fullHeight);
	svgTranslate = [fullWidth / 2 - svgScale * midX, fullHeight / 2 - svgScale * midY];

	svg.attr("transform", "translate(" + svgTranslate[0] + ", " + svgTranslate[1] + ") scale(" + svgScale + ")");
//        smoothTransform(svgTranslate, svgScale);
}

init(_selector, _options);

return {
	appendRandomDataToNode: appendRandomDataToNode,
	neo4jDataToD3Data: neo4jDataToD3Data,
	randomD3Data: randomD3Data,
	size: size,
	updateWithD3Data: updateWithD3Data,
	updateWithNeo4jData: updateWithNeo4jData,
	version: version
};
}

export default d3Neo4J

