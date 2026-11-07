import { GLTFLoader } from "./vendor/addons/loaders/GLTFLoader.js";

// Render-only adapter: never changes driving state, collision bounds or lap rules.
export async function loadVehicle() {
  const gltf = await new GLTFLoader().loadAsync(
    "./assets/library/retro-coupe.glb",
  );
  const root = gltf.scene;
  const wheels = ["frontLeft", "frontRight", "rearLeft", "rearRight"].map(
    (name) => ({
      steer: root.getObjectByName(`${name}Steer`),
      spin: root.getObjectByName(`${name}Spin`),
      front: name.startsWith("front"),
    }),
  );
  function dispose() {
    const geometries = new Set(),
      materials = new Set(),
      textures = new Set();
    root.traverse((o) => {
      if (o.geometry) geometries.add(o.geometry);
      for (const m of o.material
        ? Array.isArray(o.material)
          ? o.material
          : [o.material]
        : []) {
        materials.add(m);
        for (const value of Object.values(m))
          if (value?.isTexture) textures.add(value);
      }
    });
    geometries.forEach((g) => g.dispose());
    materials.forEach((m) => m.dispose());
    textures.forEach((t) => {
      t.dispose();
      t.source?.data?.close?.();
    });
  }
  if (wheels.some((w) => !w.steer || !w.spin)) {
    dispose();
    throw Error("Vehicle wheel pivots missing");
  }
  if (!root.getObjectByName("Body")?.material?.map) {
    dispose();
    throw Error("Vehicle paint texture missing");
  }
  root.traverse((o) => {
    if (o.isMesh) {
      o.castShadow = true;
      o.receiveShadow = true;
      o.frustumCulled = false;
    }
  });
  let rotation = 0;
  return {
    root,
    dispose,
    update(speed, steer, dt) {
      rotation = (rotation - (speed * dt) / 0.263) % (Math.PI * 2);
      for (const w of wheels) {
        w.steer.rotation.y = w.front ? -steer * 0.35 : 0;
        w.spin.rotation.x = rotation;
      }
    },
    status: () => ({
      wheels: wheels.length,
      rotation,
      frontSteer: wheels[0].steer.rotation.y,
      textured: !!root.getObjectByName("Body")?.material?.map,
    }),
  };
}
