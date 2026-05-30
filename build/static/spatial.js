/* Spatial view: three.js 3D room with a presence orb on a distance ring,
 * plus a range-Doppler heatmap and a range-time waterfall.
 *
 * HONEST LIMITS (kept visible in the UI): one speaker + one mic gives DISTANCE
 * and radial VELOCITY only -- no angle. The orb is placed on a default heading
 * and a full ring is drawn at the target radius to show the true ambiguity.
 */
window.Spatial = (function () {
  "use strict";

  var THREE = window.THREE;
  var ok = !!THREE;

  // ---- three.js scene ----
  var scene, camera, renderer, orb, orbHalo, ring, trail;
  var trailPts = [];
  var TRAIL_MAX = 40;
  var roomMax = 4.0;          // meters mapped to scene extent
  var pulseT = 0;

  function initThree() {
    var host = document.getElementById("three");
    if (!host || !ok) {
      if (host) host.innerHTML =
        '<div class="three-fallback">3D view needs three.min.js</div>';
      return;
    }
    var w = host.clientWidth, h = host.clientHeight || 320;
    scene = new THREE.Scene();
    scene.background = new THREE.Color(0x0d1117);

    camera = new THREE.PerspectiveCamera(50, w / h, 0.1, 100);
    camera.position.set(0, 5.2, 5.6);
    camera.lookAt(0, 0, 1.8);

    renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setPixelRatio(window.devicePixelRatio || 1);
    renderer.setSize(w, h);
    host.appendChild(renderer.domElement);

    scene.add(new THREE.AmbientLight(0x404a5a, 1.2));
    var pl = new THREE.PointLight(0x88aaff, 0.8);
    pl.position.set(0, 6, 2);
    scene.add(pl);

    // floor grid (the room), laptop at origin
    var grid = new THREE.GridHelper(2 * roomMax, 16, 0x30363d, 0x21262d);
    scene.add(grid);

    var lap = new THREE.Mesh(
      new THREE.BoxGeometry(0.5, 0.12, 0.35),
      new THREE.MeshStandardMaterial({ color: 0x58a6ff, emissive: 0x12243a }));
    lap.position.set(0, 0.06, 0);
    scene.add(lap);

    // distance ring (the ambiguity locus at the target radius)
    var ringGeo = new THREE.RingGeometry(0.98, 1.02, 64);
    var ringMat = new THREE.MeshBasicMaterial(
      { color: 0x58a6ff, side: THREE.DoubleSide, transparent: true, opacity: 0.35 });
    ring = new THREE.Mesh(ringGeo, ringMat);
    ring.rotation.x = -Math.PI / 2;
    ring.position.y = 0.02;
    ring.visible = false;
    scene.add(ring);

    // presence orb + halo
    orb = new THREE.Mesh(
      new THREE.SphereGeometry(0.18, 24, 24),
      new THREE.MeshStandardMaterial(
        { color: 0x9fb4c8, emissive: 0x223044, emissiveIntensity: 1.0 }));
    orb.position.set(0, 0.3, 1.5);
    orb.visible = false;
    scene.add(orb);

    var haloMat = new THREE.SpriteMaterial(
      { map: makeHalo(), color: 0xffffff, transparent: true,
        blending: THREE.AdditiveBlending, opacity: 0.8, depthWrite: false });
    orbHalo = new THREE.Sprite(haloMat);
    orbHalo.scale.set(1.2, 1.2, 1);
    orb.add(orbHalo);

    // motion trail
    var tgeo = new THREE.BufferGeometry();
    tgeo.setAttribute("position",
      new THREE.BufferAttribute(new Float32Array(TRAIL_MAX * 3), 3));
    trail = new THREE.Line(tgeo,
      new THREE.LineBasicMaterial({ color: 0x58a6ff, transparent: true, opacity: 0.5 }));
    scene.add(trail);

    window.addEventListener("resize", onResize);
    animate();
  }

  function makeHalo() {
    var c = document.createElement("canvas");
    c.width = c.height = 128;
    var g = c.getContext("2d");
    var grad = g.createRadialGradient(64, 64, 4, 64, 64, 64);
    grad.addColorStop(0, "rgba(255,255,255,0.9)");
    grad.addColorStop(0.3, "rgba(160,200,255,0.5)");
    grad.addColorStop(1, "rgba(0,0,0,0)");
    g.fillStyle = grad;
    g.fillRect(0, 0, 128, 128);
    var tex = new THREE.CanvasTexture(c);
    return tex;
  }

  function onResize() {
    var host = document.getElementById("three");
    if (!host || !renderer) return;
    var w = host.clientWidth, h = host.clientHeight || 320;
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    renderer.setSize(w, h);
  }

  function velColor(v) {
    // blue approaching (v<0), red receding (v>0), grey near-still
    var a = Math.min(1, Math.abs(v) / 1.0);
    if (Math.abs(v) < 0.05) return new THREE.Color(0x9fb4c8);
    return v < 0 ? new THREE.Color(0.4 - 0.4 * a, 0.6, 1.0)
                 : new THREE.Color(1.0, 0.45 - 0.3 * a, 0.32);
  }

  var lastTarget = null, breathBpm = 0;

  function updateScene(target, bpm) {
    lastTarget = target;
    breathBpm = bpm || 0;
    if (!ok || !orb) return;
    if (!target || !target.present) {
      orb.visible = false; ring.visible = false;
      return;
    }
    // map range (m) into scene radius, clamp to room
    var r = Math.min(target.range_m, roomMax);
    orb.visible = true; ring.visible = true;
    // default heading +Z (angle unknown); ring shows the ambiguity locus
    orb.position.set(0, 0.3, r);
    ring.scale.set(r, r, r);

    var col = velColor(target.velocity);
    orb.material.color.copy(col);
    orb.material.emissive.copy(col).multiplyScalar(0.35);
    var s = 0.6 + 1.4 * Math.min(1, target.strength || 0);
    orbHalo.material.color.copy(col);
    orbHalo.scale.set(s, s, 1);

    // trail
    trailPts.push(new THREE.Vector3(0, 0.3, r));
    if (trailPts.length > TRAIL_MAX) trailPts.shift();
    var pos = trail.geometry.attributes.position.array;
    for (var i = 0; i < TRAIL_MAX; i++) {
      var p = trailPts[i] || trailPts[0] || new THREE.Vector3();
      pos[i * 3] = p.x; pos[i * 3 + 1] = p.y; pos[i * 3 + 2] = p.z;
    }
    trail.geometry.attributes.position.needsUpdate = true;
  }

  function animate() {
    requestAnimationFrame(animate);
    if (!renderer) return;
    if (orb && orb.visible) {
      // breathing pulse when we have a rate (Hz), else gentle idle pulse
      var rateHz = breathBpm > 0 ? breathBpm / 60.0 : 0.25;
      pulseT += rateHz * 2 * Math.PI * 0.016;   // ~60fps step
      orb.scale.setScalar(1 + 0.08 * Math.sin(pulseT));
    }
    renderer.render(scene, camera);
  }

  // ---- range-Doppler heatmap ----
  var rdmCanvas, rdmCtx;
  function viridis(t) {
    // compact viridis-ish ramp, t in 0..1
    t = Math.max(0, Math.min(1, t));
    var r = Math.round(255 * Math.min(1, Math.max(0, -0.2 + 2.0 * t)));
    var g = Math.round(255 * Math.min(1, Math.max(0, 0.1 + 0.9 * t)));
    var b = Math.round(255 * Math.min(1, Math.max(0, 0.9 - 0.9 * Math.abs(t - 0.4))));
    return [r, g, b];
  }

  function drawRDM(rdm) {
    if (!rdmCtx || !rdm || !rdm.data || !rdm.data.length) return;
    var nd = rdm.nd, nr = rdm.nr;
    var img = rdmCtx.createImageData(nr, nd);
    for (var i = 0; i < nd * nr; i++) {
      var c = viridis(rdm.data[i] / 255);
      img.data[i * 4] = c[0]; img.data[i * 4 + 1] = c[1];
      img.data[i * 4 + 2] = c[2]; img.data[i * 4 + 3] = 255;
    }
    // scale the small image up to the canvas
    var tmp = document.createElement("canvas");
    tmp.width = nr; tmp.height = nd;
    tmp.getContext("2d").putImageData(img, 0, 0);
    rdmCtx.imageSmoothingEnabled = true;
    rdmCtx.clearRect(0, 0, rdmCanvas.width, rdmCanvas.height);
    rdmCtx.drawImage(tmp, 0, 0, rdmCanvas.width, rdmCanvas.height);
  }

  // ---- range-time waterfall ----
  var wfCanvas, wfCtx, wfImg;
  function pushWaterfall(prof) {
    if (!wfCtx || !prof || !prof.length) return;
    var w = wfCanvas.width, h = wfCanvas.height;
    // scroll existing image up by 1px
    var prev = wfCtx.getImageData(0, 1, w, h - 1);
    wfCtx.putImageData(prev, 0, 0);
    // draw newest row at the bottom
    var row = wfCtx.createImageData(w, 1);
    for (var x = 0; x < w; x++) {
      var idx = Math.floor(x / w * prof.length);
      var c = viridis(prof[idx]);
      row.data[x * 4] = c[0]; row.data[x * 4 + 1] = c[1];
      row.data[x * 4 + 2] = c[2]; row.data[x * 4 + 3] = 255;
    }
    wfCtx.putImageData(row, 0, h - 1);
  }

  function sizeCanvas(cv) {
    var dpr = window.devicePixelRatio || 1;
    cv.width = cv.clientWidth * dpr;
    cv.height = 180 * dpr;
  }

  function init() {
    initThree();
    rdmCanvas = document.getElementById("rdm");
    wfCanvas = document.getElementById("waterfall");
    if (rdmCanvas) { sizeCanvas(rdmCanvas); rdmCtx = rdmCanvas.getContext("2d"); }
    if (wfCanvas) {
      sizeCanvas(wfCanvas); wfCtx = wfCanvas.getContext("2d");
      wfCtx.fillStyle = "#0d1117";
      wfCtx.fillRect(0, 0, wfCanvas.width, wfCanvas.height);
    }
  }

  function update(d) {
    if (d.target) updateScene(d.target, d.breathing && d.breathing.bpm);
    if (d.rdm) drawRDM(d.rdm);
    if (d.range && d.range.prof) pushWaterfall(d.range.prof);
    if (d.range && d.range.max_m) {
      var el = document.getElementById("rdmMax");
      if (el) el.textContent = d.range.max_m.toFixed(1) + " m →";
    }
    if (d.rdm && d.rdm.v_max) {
      var v = document.getElementById("vmaxNote");
      if (v) v.textContent = "~" + d.rdm.v_max.toFixed(2);
    }
  }

  return { init: init, update: update };
})();
