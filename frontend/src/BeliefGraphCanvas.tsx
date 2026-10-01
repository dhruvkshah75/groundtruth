import { useEffect, useRef } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { CSS2DObject, CSS2DRenderer } from "three/addons/renderers/CSS2DRenderer.js";
import type { DisplayFact } from "./BeliefGraphPage";
import { graphPositions } from "./beliefGraphLayout";

export type CameraAction = {
  id: number;
  type: "zoom-in" | "zoom-out" | "reset";
};

type Props = {
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
  interactionMode,
  selectedFactId,
  selectedNodeId,
  highlightedFactIds,
  cameraAction,
  onSelectFact,
  onSelectNode,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const cameraRef = useRef<THREE.PerspectiveCamera | null>(null);
  const controlsRef = useRef<OrbitControls | null>(null);
  const cameraPositionRef = useRef(DEFAULT_CAMERA.clone());
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
    scene.background = new THREE.Color(0x1e2220);
    scene.fog = new THREE.FogExp2(0x1e2220, 0.035);

    const camera = new THREE.PerspectiveCamera(48, width / height, 0.1, 100);
    camera.position.copy(cameraPositionRef.current);
    cameraRef.current = camera;

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(width, height);
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.domElement.className = "belief-webgl-canvas";
    container.appendChild(renderer.domElement);

    const labelRenderer = new CSS2DRenderer();
    labelRenderer.setSize(width, height);
    labelRenderer.domElement.className = "belief-label-layer";
    container.appendChild(labelRenderer.domElement);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.07;
    controls.minDistance = 4;
    controls.maxDistance = 24;
    controls.enablePan = true;
    controls.target.copy(cameraTargetRef.current);
    controls.mouseButtons.LEFT = interactionMode === "pan" ? THREE.MOUSE.PAN : THREE.MOUSE.ROTATE;
    controls.mouseButtons.RIGHT = interactionMode === "pan" ? THREE.MOUSE.ROTATE : THREE.MOUSE.PAN;
    controls.touches.ONE = interactionMode === "pan" ? THREE.TOUCH.PAN : THREE.TOUCH.ROTATE;
    controlsRef.current = controls;

    scene.add(new THREE.AmbientLight(0xa8d8cd, 1.25));
    const keyLight = new THREE.PointLight(0x61d8ca, 22, 35);
    keyLight.position.set(3, 5, 7);
    scene.add(keyLight);
    const fillLight = new THREE.PointLight(0x7d958f, 12, 30);
    fillLight.position.set(-6, -3, -4);
    scene.add(fillLight);

    const dustGeometry = new THREE.BufferGeometry();
    const dustPoints: number[] = [];
    for (let index = 0; index < 180; index += 1) {
      const angle = index * 2.39996;
      const radius = 3 + (index % 29) * 0.31;
      dustPoints.push(
        Math.cos(angle) * radius,
        ((index * 17) % 37) * 0.32 - 5.8,
        Math.sin(angle) * radius,
      );
    }
    dustGeometry.setAttribute("position", new THREE.Float32BufferAttribute(dustPoints, 3));
    scene.add(new THREE.Points(
      dustGeometry,
      new THREE.PointsMaterial({ color: 0x76988f, size: 0.025, transparent: true, opacity: 0.34 }),
    ));

    const positions = graphPositions(nodes, facts);
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
        color: 0x315a50,
        emissive: 0x17362f,
        emissiveIntensity: 0.8,
        roughness: 0.46,
        metalness: 0.05,
      });
      const mesh = new THREE.Mesh(new THREE.SphereGeometry(radius, 30, 22), material);
      const position = positions.get(node) ?? { x: 0, y: 0, z: 0 };
      mesh.position.set(position.x, position.y, position.z);
      mesh.userData = { nodeId: node };
      scene.add(mesh);

      const halo = new THREE.Mesh(
        new THREE.SphereGeometry(radius * 1.55, 24, 16),
        new THREE.MeshBasicMaterial({ color: 0x61d8ca, transparent: true, opacity: 0.04, side: THREE.BackSide }),
      );
      mesh.add(halo);
      const label = makeLabel(node, `belief-node-label${nodeDegree >= 3 ? " hub" : ""}`);
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
        color: fact.active ? 0x4f8f83 : 0x666b69,
        transparent: true,
        opacity: fact.active ? 0.76 : 0.42,
      });
      const line = new THREE.Line(lineGeometry, lineMaterial);
      line.userData = { factId: fact.factId };
      scene.add(line);

      const arrow = new THREE.Mesh(
        new THREE.ConeGeometry(0.055, 0.18, 10),
        new THREE.MeshBasicMaterial({ color: fact.active ? 0x61a99b : 0x6b6f6d, transparent: true, opacity: 0.8 }),
      );
      arrow.position.copy(end);
      arrow.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), normalized);
      arrow.userData = { factId: fact.factId };
      scene.add(arrow);

      const labelObject = makeLabel(fact.predicate, fact.active ? "belief-edge-label" : "belief-edge-label superseded");
      labelObject.position.copy(start).lerp(end, 0.5);
      scene.add(labelObject);
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

    const handlePointer = (event: MouseEvent) => {
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
      if (event.type !== "click" || !hit) return;
      const factId = hit.userData.factId as string | undefined;
      if (factId) {
        onSelectRef.current(factId);
        return;
      }
      const nodeId = hit.userData.nodeId as string | undefined;
      if (nodeId) onSelectNodeRef.current(nodeId);
    };
    renderer.domElement.addEventListener("pointermove", handlePointer);
    renderer.domElement.addEventListener("click", handlePointer);

    const resizeObserver = new ResizeObserver(() => {
      const nextWidth = Math.max(container.clientWidth, 1);
      const nextHeight = Math.max(container.clientHeight, 1);
      camera.aspect = nextWidth / nextHeight;
      camera.updateProjectionMatrix();
      renderer.setSize(nextWidth, nextHeight);
      labelRenderer.setSize(nextWidth, nextHeight);
    });
    resizeObserver.observe(container);

    let animationFrame = 0;
    const render = () => {
      animationFrame = window.requestAnimationFrame(render);
      controls.update();
      cameraPositionRef.current.copy(camera.position);
      cameraTargetRef.current.copy(controls.target);
      renderer.render(scene, camera);
      labelRenderer.render(scene, camera);
    };
    render();

    return () => {
      window.cancelAnimationFrame(animationFrame);
      resizeObserver.disconnect();
      renderer.domElement.removeEventListener("pointermove", handlePointer);
      renderer.domElement.removeEventListener("click", handlePointer);
      controls.dispose();
      disposeObject(scene);
      renderer.dispose();
      renderer.domElement.remove();
      labelRenderer.domElement.remove();
      cameraRef.current = null;
      controlsRef.current = null;
      nodeMeshesRef.current = new Map();
      factObjectsRef.current = new Map();
    };
  }, [graphSignature]);

  useEffect(() => {
    for (const [nodeId, mesh] of nodeMeshesRef.current) {
      const related = nodeId === selectedNodeId || facts.some(
        (fact) => fact.factId === selectedFactId && (fact.source === nodeId || fact.target === nodeId),
      );
      const material = mesh.material as THREE.MeshStandardMaterial;
      material.color.setHex(related ? 0x4c8175 : 0x315a50);
      material.emissiveIntensity = related ? 1.45 : 0.8;
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
      const color = selected ? 0xf0c77f : changed || relatedToNode ? 0x78e6d8 : rendered.active ? 0x4f8f83 : 0x666b69;
      lineMaterial.color.setHex(color);
      lineMaterial.opacity = selected || changed || relatedToNode ? 1 : rendered.active ? 0.76 : 0.42;
      arrowMaterial.color.setHex(color);
      rendered.label.classList.toggle("selected", selected);
      rendered.label.classList.toggle("changed", changed || relatedToNode);
    }
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
      camera.position.copy(DEFAULT_CAMERA);
      controls.target.set(0, 0, 0);
    } else {
      const multiplier = cameraAction.type === "zoom-in" ? 0.82 : 1.22;
      camera.position.sub(controls.target).multiplyScalar(multiplier).add(controls.target);
    }
    cameraPositionRef.current.copy(camera.position);
    cameraTargetRef.current.copy(controls.target);
    controls.update();
  }, [cameraAction]);

  return <div className="belief-canvas" ref={containerRef} aria-hidden="true" />;
}
