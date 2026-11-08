import { displayText } from "./display";
import { useEffect, useRef } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { CSS2DObject, CSS2DRenderer } from "three/addons/renderers/CSS2DRenderer.js";
import type { DisplayFact } from "./BeliefGraphPage";
import { graphPositions, type GraphPoint } from "./beliefGraphLayout";
import { factColor } from "./beliefGraphAppearance";
import { placeGraphLabels, type GraphLabel } from "./beliefGraphLabels";

export type CameraAction = {
  id: number;
  type: "zoom-in" | "zoom-out" | "reset";
};

type Props = {
  preserveLayout?: boolean;
  facts: DisplayFact[];
  nodes: string[];
  interactionMode: "orbit" | "pan";
  selectedFactId: string | null;
  selectedNodeId: string | null;
  highlightedFactIds: Set<string>;
  cameraAction: CameraAction;
  onSelectFact: (factId: string) => void;
  onSelectNode: (nodeId: string) => void;
};

type RenderedFact = {
  line: THREE.Line;
  arrow: THREE.Mesh;
  label: HTMLElement;
  active: boolean;
};

const DEFAULT_CAMERA = new THREE.Vector3(0, 1.2, 11);

function makeLabel(text: string, className: string): CSS2DObject {
  const element = document.createElement("span");
  element.className = className;
  element.textContent = text;
  return new CSS2DObject(element);
}

function disposeObject(root: THREE.Object3D): void {
  root.traverse((object) => {
    if (object instanceof THREE.Mesh || object instanceof THREE.Line || object instanceof THREE.Points) {
      object.geometry.dispose();
      const materials = Array.isArray(object.material) ? object.material : [object.material];
      materials.forEach((material) => material.dispose());
    }
  });
}

export function BeliefGraphCanvas({
  facts,
  nodes,
  preserveLayout = false,
  interactionMode,
  selectedFactId,
  selectedNodeId,
  highlightedFactIds,
  cameraAction,
  onSelectFact,
  onSelectNode,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const previousPositions = useRef<Map<string, GraphPoint>>(new Map());
  const labelsDirtyRef = useRef(true);
  const cameraRef = useRef<THREE.PerspectiveCamera | null>(null);
  const controlsRef = useRef<OrbitControls | null>(null);
  const cameraPositionRef = useRef<THREE.Vector3 | null>(null);
  const fittedCameraRef = useRef(DEFAULT_CAMERA.clone());
  const cameraTargetRef = useRef(new THREE.Vector3());
  const nodeMeshesRef = useRef(new Map<string, THREE.Mesh>());
  const factObjectsRef = useRef(new Map<string, RenderedFact>());
  const onSelectRef = useRef(onSelectFact);
  const onSelectNodeRef = useRef(onSelectNode);
  const graphSignature = JSON.stringify({
    nodes,
    facts: facts.map((fact) => [
      fact.factId,
      fact.source,
      fact.predicate,
      fact.target,
      fact.active,
      fact.confidence,
    ]),
  });

  useEffect(() => {
    onSelectRef.current = onSelectFact;
    onSelectNodeRef.current = onSelectNode;
  }, [onSelectFact, onSelectNode]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container || !nodes.length) return;

    const width = Math.max(container.clientWidth, 1);
    const height = Math.max(container.clientHeight, 1);
    const scene = new THREE.Scene();
    const theme = getComputedStyle(container);
    scene.background = new THREE.Color(theme.getPropertyValue("--surface-raised").trim() || "#f8f6f9");

    const positions = graphPositions(nodes, facts, preserveLayout ? previousPositions.current : undefined);
    if (preserveLayout) previousPositions.current = positions;
    const radius = Math.max(1, ...[...positions.values()].map(({ x, y, z }) => Math.hypot(x, y, z))) + 0.5;
    const fitDistance = (aspect: number) => {
      const halfFov = Math.min(THREE.MathUtils.degToRad(24), Math.atan(Math.tan(THREE.MathUtils.degToRad(24)) * aspect));
      return radius / Math.sin(halfFov) * 1.1;
    };
    let previousFit = fitDistance(width / height);
    fittedCameraRef.current.copy(DEFAULT_CAMERA).normalize().multiplyScalar(previousFit);
    const camera = new THREE.PerspectiveCamera(48, width / height, 0.1, 200);
    camera.position.copy(cameraPositionRef.current ?? fittedCameraRef.current);
    cameraRef.current = camera;

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(width, height);
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.domElement.className = "belief-webgl-canvas";
    container.appendChild(renderer.domElement);

    const connectorLayer = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    connectorLayer.classList.add("belief-label-connectors");
    connectorLayer.setAttribute("aria-hidden", "true");
    container.appendChild(connectorLayer);
    const connectorLines = new Map<string, SVGLineElement>();

    const labelRenderer = new CSS2DRenderer();
    labelRenderer.setSize(width, height);
    labelRenderer.domElement.className = "belief-label-layer";
    container.appendChild(labelRenderer.domElement);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.07;
    controls.minDistance = 4;
    controls.maxDistance = Math.max(40, previousFit * 2);
    controls.enablePan = true;
    controls.target.copy(cameraTargetRef.current);
    controls.mouseButtons.LEFT = interactionMode === "pan" ? THREE.MOUSE.PAN : THREE.MOUSE.ROTATE;
    controls.mouseButtons.RIGHT = interactionMode === "pan" ? THREE.MOUSE.ROTATE : THREE.MOUSE.PAN;
    controls.touches.ONE = interactionMode === "pan" ? THREE.TOUCH.PAN : THREE.TOUCH.ROTATE;
    controlsRef.current = controls;

    scene.add(new THREE.AmbientLight(0xffffff, 2));
    const keyLight = new THREE.DirectionalLight(0xffffff, 2.5);
    keyLight.position.set(3, 5, 7);
    scene.add(keyLight);

    const graphLabels: { id: string; object: CSS2DObject; kind: "node" | "edge"; anchor?: THREE.Object3D }[] = [];
    const degree = new Map(nodes.map((node) => [node, 0]));
    facts.forEach((fact) => {
      degree.set(fact.source, (degree.get(fact.source) ?? 0) + 1);
      degree.set(fact.target, (degree.get(fact.target) ?? 0) + 1);
    });

    const nodeMeshes = new Map<string, THREE.Mesh>();
    for (const node of nodes) {
      const nodeDegree = degree.get(node) ?? 0;
      const radius = 0.15 + Math.min(nodeDegree, 6) * 0.018;
      const material = new THREE.MeshStandardMaterial({
        color: document.documentElement.dataset.theme === "dark" ? 0x70afa6 : 0x765184,
        emissive: 0x000000,
        emissiveIntensity: 0,
        roughness: 0.46,
        metalness: 0.05,
      });
      const mesh = new THREE.Mesh(new THREE.SphereGeometry(radius, 30, 22), material);
      const position = positions.get(node) ?? { x: 0, y: 0, z: 0 };
      mesh.position.set(position.x, position.y, position.z);
      mesh.userData = { nodeId: node };
      scene.add(mesh);

      const label = makeLabel(displayText(node), "belief-node-label");
      const name = document.createElement("strong");
      name.textContent = displayText(node);
      const detail = document.createElement("small");
      detail.textContent = nodeDegree ? `${nodeDegree} connected ${nodeDegree === 1 ? "fact" : "facts"}` : "No active relationships";
      label.element.replaceChildren(name, detail);
      label.element.title = `${displayText(node)} · ${detail.textContent}`;
      graphLabels.push({ id: `node:${node}`, object: label, kind: "node", anchor: mesh });
      const connector = document.createElementNS("http://www.w3.org/2000/svg", "line");
      connectorLayer.appendChild(connector);
      connectorLines.set(`node:${node}`, connector);
      label.position.set(0, radius + 0.16, 0);
      mesh.add(label);
      mesh.userData.labelElement = label.element;
      nodeMeshes.set(node, mesh);
    }
    nodeMeshesRef.current = nodeMeshes;

    const factObjects = new Map<string, RenderedFact>();
    for (const fact of facts) {
      const sourcePoint = positions.get(fact.source);
      const targetPoint = positions.get(fact.target);
      if (!sourcePoint || !targetPoint) continue;
      const source = new THREE.Vector3(sourcePoint.x, sourcePoint.y, sourcePoint.z);
      const target = new THREE.Vector3(targetPoint.x, targetPoint.y, targetPoint.z);

      const direction = new THREE.Vector3().subVectors(target, source);
      const length = direction.length();
      if (length === 0) continue;
      const normalized = direction.clone().normalize();
      const start = source.clone().addScaledVector(normalized, 0.27);
      const end = target.clone().addScaledVector(normalized, -0.3);
      const lineGeometry = new THREE.BufferGeometry().setFromPoints([start, end]);
      const lineMaterial = new THREE.LineBasicMaterial({
        color: factColor(fact.active, false, false, false, document.documentElement.dataset.theme === "dark"),
        transparent: true,
        opacity: fact.active ? 0.95 : 0.7,
      });
      const line = new THREE.Line(lineGeometry, lineMaterial);
      line.userData = { factId: fact.factId };
      scene.add(line);

      const arrow = new THREE.Mesh(
        new THREE.ConeGeometry(0.055, 0.18, 10),
        new THREE.MeshBasicMaterial({ color: factColor(fact.active, false, false, false, document.documentElement.dataset.theme === "dark"), transparent: true, opacity: 0.8 }),
      );
      arrow.position.copy(end);
      arrow.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), normalized);
      arrow.userData = { factId: fact.factId };
      scene.add(arrow);

      const labelObject = makeLabel(displayText(fact.predicate), fact.active ? "belief-edge-label" : "belief-edge-label superseded");
      labelObject.position.copy(start).lerp(end, 0.5);
      scene.add(labelObject);
      labelObject.element.title = `${displayText(fact.source)} ${displayText(fact.predicate)} ${displayText(fact.target)} · ${Math.round(fact.confidence * 100)}% confidence · ${displayText(fact.sourceAgent)}`;
      graphLabels.push({ id: `fact:${fact.factId}`, object: labelObject, kind: "edge" });
      factObjects.set(fact.factId, { line, arrow, label: labelObject.element, active: fact.active });
    }
    factObjectsRef.current = factObjects;

    const raycaster = new THREE.Raycaster();
    raycaster.params.Line = { threshold: 0.18 };
    const pointer = new THREE.Vector2();
    const interactive = [
      ...nodeMeshes.values(),
      ...[...factObjects.values()].flatMap(({ line, arrow }) => [line, arrow]),
    ];

    let pointerDown: { x: number; y: number } | null = null;
    const handlePointerDown = (event: PointerEvent) => {
      pointerDown = { x: event.clientX, y: event.clientY };
    };
    const handlePointer = (event: MouseEvent) => {
      // Orbit and pan gestures must not also select the node under the pointer.
      if (event.type === "click" && pointerDown
        && Math.hypot(event.clientX - pointerDown.x, event.clientY - pointerDown.y) > 5) return;
      const bounds = renderer.domElement.getBoundingClientRect();
      pointer.x = ((event.clientX - bounds.left) / bounds.width) * 2 - 1;
      pointer.y = -((event.clientY - bounds.top) / bounds.height) * 2 + 1;
      raycaster.setFromCamera(pointer, camera);
      const hit = raycaster.intersectObjects(interactive, false)[0]?.object;
      renderer.domElement.style.cursor = hit ? "pointer" : "grab";
      for (const mesh of nodeMeshes.values()) {
        (mesh.userData.labelElement as HTMLElement).classList.remove("hovered");
      }
      if (hit?.userData.nodeId) {
        (hit.userData.labelElement as HTMLElement).classList.add("hovered");
      }
      for (const [factId, rendered] of factObjects) {
        rendered.label.classList.toggle("hovered", hit?.userData.factId === factId);
      }
      labelsDirtyRef.current = true;
      if (event.type !== "click" || !hit) return;
      const factId = hit.userData.factId as string | undefined;
      if (factId) {
        onSelectRef.current(factId);
        return;
      }
      const nodeId = hit.userData.nodeId as string | undefined;
      if (nodeId) onSelectNodeRef.current(nodeId);
    };
    renderer.domElement.addEventListener("pointerdown", handlePointerDown);
    renderer.domElement.addEventListener("pointermove", handlePointer);
    renderer.domElement.addEventListener("click", handlePointer);

    const resizeObserver = new ResizeObserver(() => {
      const nextWidth = Math.max(container.clientWidth, 1);
      const nextHeight = Math.max(container.clientHeight, 1);
      camera.aspect = nextWidth / nextHeight;
      const nextFit = fitDistance(camera.aspect);
      camera.position.sub(controls.target).multiplyScalar(nextFit / previousFit).add(controls.target);
      fittedCameraRef.current.copy(DEFAULT_CAMERA).normalize().multiplyScalar(nextFit);
      controls.maxDistance = Math.max(40, nextFit * 2);
      previousFit = nextFit;
      camera.updateProjectionMatrix();
      renderer.setSize(nextWidth, nextHeight);
      labelRenderer.setSize(nextWidth, nextHeight);
      labelsDirtyRef.current = true;
    });
    resizeObserver.observe(container);

    const projected = new THREE.Vector3();
    const layoutLabels = () => {
      const screenLabels: GraphLabel[] = [];
      for (const { id, object, kind } of graphLabels) {
        object.getWorldPosition(projected).project(camera);
        const element = object.element;
        if (projected.z < -1 || projected.z > 1) { element.style.visibility = "hidden"; continue; }
        const focused = element.classList.contains("hovered") || element.classList.contains("selected");
        screenLabels.push({ id, kind, x: (projected.x + 1) * container.clientWidth / 2,
          y: (1 - projected.y) * container.clientHeight / 2,
          width: element.offsetWidth, height: element.offsetHeight,
          priority: focused ? 4 : kind === "node" ? 3 : element.classList.contains("changed") ? 2 : 1 });
      }
      const placements = placeGraphLabels(screenLabels, container.clientWidth, container.clientHeight);
      for (const { id, object, anchor } of graphLabels) {
        const placement = placements.get(id);
        object.element.style.visibility = placement?.visible ? "visible" : "hidden";
        object.element.style.marginLeft = `${placement?.x ?? 0}px`;
        object.element.style.marginTop = `${placement?.y ?? 0}px`;
        const connector = connectorLines.get(id);
        if (connector && anchor) {
          const displaced = placement?.visible && Math.hypot(placement.x, placement.y) > 8;
          connector.style.display = displaced ? "" : "none";
          if (displaced) {
            anchor.getWorldPosition(projected).project(camera);
            connector.setAttribute("x1", String((projected.x + 1) * container.clientWidth / 2));
            connector.setAttribute("y1", String((1 - projected.y) * container.clientHeight / 2));
            object.getWorldPosition(projected).project(camera);
            connector.setAttribute("x2", String((projected.x + 1) * container.clientWidth / 2 + placement.x));
            connector.setAttribute("y2", String((1 - projected.y) * container.clientHeight / 2 + placement.y));
          }
        }
      }
      labelsDirtyRef.current = false;
    };
    labelsDirtyRef.current = true;
    let animationFrame = 0;
    const render = () => {
      animationFrame = window.requestAnimationFrame(render);
      const cameraMoved = controls.update();
      (cameraPositionRef.current ??= new THREE.Vector3()).copy(camera.position);
      cameraTargetRef.current.copy(controls.target);
      renderer.render(scene, camera);
      labelRenderer.render(scene, camera);
      if (cameraMoved || labelsDirtyRef.current) layoutLabels();
    };
    render();

    // Update materials in place so a theme switch preserves the camera and selection.
    const themeObserver = new MutationObserver(() => {
      const dark = document.documentElement.dataset.theme === "dark";
      scene.background = new THREE.Color(getComputedStyle(container).getPropertyValue("--surface-raised").trim());
      for (const mesh of nodeMeshesRef.current.values()) {
        const selected = (mesh.userData.labelElement as HTMLElement).classList.contains("selected");
        (mesh.material as THREE.MeshStandardMaterial).color.setHex(dark ? (selected ? 0x91bdb3 : 0x70afa6) : (selected ? 0x563064 : 0x765184));
      }
      for (const rendered of factObjectsRef.current.values()) {
        const color = factColor(rendered.active, rendered.label.classList.contains("selected"), false, rendered.label.classList.contains("changed"), dark);
        (rendered.line.material as THREE.LineBasicMaterial).color.setHex(color);
        (rendered.arrow.material as THREE.MeshBasicMaterial).color.setHex(color);
      }
    });
    themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });

    return () => {
      themeObserver.disconnect();
      window.cancelAnimationFrame(animationFrame);
      resizeObserver.disconnect();
      renderer.domElement.removeEventListener("pointerdown", handlePointerDown);
      renderer.domElement.removeEventListener("pointermove", handlePointer);
      renderer.domElement.removeEventListener("click", handlePointer);
      controls.dispose();
      disposeObject(scene);
      renderer.dispose();
      renderer.domElement.remove();
      labelRenderer.domElement.remove();
      connectorLayer.remove();
      cameraRef.current = null;
      controlsRef.current = null;
      nodeMeshesRef.current = new Map();
      factObjectsRef.current = new Map();
    };
  }, [graphSignature, preserveLayout]);

  useEffect(() => {
    for (const [nodeId, mesh] of nodeMeshesRef.current) {
      const related = nodeId === selectedNodeId || facts.some(
        (fact) => fact.factId === selectedFactId && (fact.source === nodeId || fact.target === nodeId),
      );
      const material = mesh.material as THREE.MeshStandardMaterial;
      const isolated = !facts.some((fact) => fact.source === nodeId || fact.target === nodeId);
      material.color.setHex(isolated && !related ? 0x817a85 : document.documentElement.dataset.theme === "dark" ? (related ? 0x91bdb3 : 0x70afa6) : (related ? 0x563064 : 0x765184));
      material.emissiveIntensity = 0;
      mesh.scale.setScalar(related ? 1.12 : 1);
      (mesh.userData.labelElement as HTMLElement).classList.toggle("selected", related);
    }
    for (const [factId, rendered] of factObjectsRef.current) {
      const selected = factId === selectedFactId;
      const fact = facts.find((candidate) => candidate.factId === factId);
      const relatedToNode = Boolean(
        selectedNodeId && fact && (fact.source === selectedNodeId || fact.target === selectedNodeId),
      );
      const changed = highlightedFactIds.has(factId);
      const lineMaterial = rendered.line.material as THREE.LineBasicMaterial;
      const arrowMaterial = rendered.arrow.material as THREE.MeshBasicMaterial;
      const color = factColor(rendered.active, selected, relatedToNode, changed, document.documentElement.dataset.theme === "dark");
      lineMaterial.color.setHex(color);
      lineMaterial.opacity = selected || changed || relatedToNode ? 1 : rendered.active ? 0.95 : 0.7;
      arrowMaterial.color.setHex(color);
      rendered.label.classList.toggle("selected", selected || relatedToNode);
      rendered.label.classList.toggle("changed", changed);
    }
    labelsDirtyRef.current = true;
  }, [facts, highlightedFactIds, selectedFactId, selectedNodeId]);

  useEffect(() => {
    const controls = controlsRef.current;
    if (!controls) return;
    controls.mouseButtons.LEFT = interactionMode === "pan" ? THREE.MOUSE.PAN : THREE.MOUSE.ROTATE;
    controls.mouseButtons.RIGHT = interactionMode === "pan" ? THREE.MOUSE.ROTATE : THREE.MOUSE.PAN;
    controls.touches.ONE = interactionMode === "pan" ? THREE.TOUCH.PAN : THREE.TOUCH.ROTATE;
  }, [interactionMode]);

  useEffect(() => {
    const camera = cameraRef.current;
    const controls = controlsRef.current;
    if (!camera || !controls || cameraAction.id === 0) return;
    if (cameraAction.type === "reset") {
      camera.position.copy(fittedCameraRef.current);
      controls.target.set(0, 0, 0);
    } else {
      const multiplier = cameraAction.type === "zoom-in" ? 0.82 : 1.22;
      camera.position.sub(controls.target).multiplyScalar(multiplier).add(controls.target);
    }
    (cameraPositionRef.current ??= new THREE.Vector3()).copy(camera.position);
    cameraTargetRef.current.copy(controls.target);
    controls.update();
  }, [cameraAction]);

  return <div className="belief-canvas" ref={containerRef} aria-hidden="true" />;
}
