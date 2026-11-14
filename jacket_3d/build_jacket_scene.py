"""
Blender-Szene fuer das 3D-Modell der weissen Puffer-Jacke.

Aufruf (headless):
  blender -b -P build_jacket_scene.py -- glb=jacket.glb out=jacket_scene.blend \
      logo=logo.png logo_pos=chest_left finish=print render=front,threequarter

Was das Skript macht:
  * importiert das GLB, fasst alle Teile zu "Jacket" zusammen, skaliert auf echte Groesse
  * verbessert das Material (mattes Nylon mit Sheen + feine Gewebestruktur)
  * baut ein Foto-Studio (Hohlkehle, Softboxen, Kameras)
  * legt Logos als Projektions-Decals an: Ein Bild-Empty ("Logo_*") zeigt das Logo im
    Viewport. Verschieben/Drehen/Skalieren des Empty verschiebt das Logo auf der Jacke.
    Das Logo folgt dabei der Form (Steppung, Falten) und reagiert auf Licht.
"""
import bpy, bmesh, sys, os, math
from mathutils import Vector

# ---------------------------------------------------------------- args
argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
A = dict(a.split("=", 1) for a in argv)
GLB = A["glb"]
OUT = A["out"]
LOGOS = [p for p in A.get("logo", "").split(",") if p]
LOGO_POS = [p for p in A.get("logo_pos", "chest_left").split(",") if p]
FINISH = A.get("finish", "print")            # print | embroidery | patch
RENDER = [c for c in A.get("render", "").split(",") if c]
SAMPLES = int(A.get("samples", "128"))
RES_X, RES_Y = (int(v) for v in A.get("res", "1600x2000").split("x"))
HEIGHT = float(A.get("height", "0.80"))      # Gesamthoehe inkl. Kapuze in m
ROT_Z = float(A.get("rotz", "0"))            # Korrektur, falls das Modell nicht nach vorne schaut
RENDER_DIR = A.get("render_dir", os.path.join(os.path.dirname(OUT), "renders"))
TAG = A.get("tag", "")


def sock(coll, ident):
    for s in coll:
        if s.identifier == ident:
            return s
    raise KeyError(ident)


# ---------------------------------------------------------------- reset + import
bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
bpy.ops.import_scene.gltf(filepath=GLB)

meshes = [o for o in scene.objects if o.type == "MESH"]
for o in meshes:
    mw = o.matrix_world.copy()
    o.parent = None
    o.matrix_world = mw
for o in list(scene.objects):
    if o.type != "MESH":
        bpy.data.objects.remove(o, do_unlink=True)

bpy.ops.object.select_all(action="DESELECT")
for o in meshes:
    o.select_set(True)
bpy.context.view_layer.objects.active = meshes[0]
if len(meshes) > 1:
    bpy.ops.object.join()
jacket = bpy.context.view_layer.objects.active
jacket.name = "Jacket"
jacket.data.name = "Jacket_Mesh"
bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
jacket.rotation_mode = "XYZ"  # glTF-Import setzt Quaternion
jacket.rotation_euler = (0.0, 0.0, math.radians(ROT_Z))
bpy.ops.object.transform_apply(location=False, rotation=True, scale=False)


def world_bbox(o):
    pts = [o.matrix_world @ Vector(c) for c in o.bound_box]
    mn = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    mx = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    return mn, mx


mn, mx = world_bbox(jacket)
s = HEIGHT / (mx.z - mn.z)
jacket.scale = (s, s, s)
bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
mn, mx = world_bbox(jacket)
HOVER = 0.22  # Ghost-Mannequin schwebt ueber dem Boden
jacket.location -= Vector(((mn.x + mx.x) / 2, (mn.y + mx.y) / 2, mn.z - HOVER))
bpy.ops.object.transform_apply(location=True, rotation=False, scale=False)
mn, mx = world_bbox(jacket)
CENTER = (mn + mx) / 2
print("JACKET_BBOX", tuple(round(v, 3) for v in mn), tuple(round(v, 3) for v in mx),
      "verts", len(jacket.data.vertices))

for p in jacket.data.polygons:
    p.use_smooth = True

# ---------------------------------------------------------------- material
mats = [m for m in jacket.data.materials if m and m.use_nodes]
JACKET_COLOR = tuple(float(v) for v in A.get("color", "0.80,0.80,0.775").split(",")) + (1.0,)


def principled(mat):
    return next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")


def ensure_source(mat, inp, node_type, value_setter):
    """Gibt den Socket zurueck, der gerade in `inp` fliesst (legt einen Konstanten-Node an, falls leer)."""
    if inp.is_linked:
        return inp.links[0].from_socket
    n = mat.node_tree.nodes.new(node_type)
    value_setter(n)
    mat.node_tree.links.new(n.outputs[0], inp)
    return n.outputs[0]


for mat in mats:
    nt, N, L = mat.node_tree, mat.node_tree.nodes, mat.node_tree.links
    b = principled(mat)
    # Keine Selbstleuchten: generierte Emission-Maps machen den Stoff flach und "glowy"
    for l in list(b.inputs["Emission Color"].links):
        L.remove(l)
    b.inputs["Emission Strength"].default_value = 0.0
    # Stoff ist nicht metallisch -> generierte Metallic-Maps entfernen
    for l in list(b.inputs["Metallic"].links):
        L.remove(l)
    b.inputs["Metallic"].default_value = 0.0
    # Farbtextur saeubern: dunkle Artefakt-Punkte anheben, Ton zu natuerlichem Off-White vereinheitlichen
    bc_in = b.inputs["Base Color"]
    if bc_in.is_linked:
        src = bc_in.links[0].from_socket
        lift = N.new("ShaderNodeMix")
        lift.data_type, lift.blend_type = "RGBA", "LIGHTEN"
        lift.label = "Remove dark specks"
        sock(lift.inputs, "Factor_Float").default_value = 1.0
        L.new(src, sock(lift.inputs, "A_Color"))
        sock(lift.inputs, "B_Color").default_value = (0.62, 0.62, 0.61, 1)
        tone = N.new("ShaderNodeMix")
        tone.data_type, tone.blend_type = "RGBA", "MIX"
        tone.label = "Jacket Color (Off-White)"
        sock(tone.inputs, "Factor_Float").default_value = 0.45
        L.new(sock(lift.outputs, "Result_Color"), sock(tone.inputs, "A_Color"))
        sock(tone.inputs, "B_Color").default_value = JACKET_COLOR
        L.new(sock(tone.outputs, "Result_Color"), bc_in)
    else:
        bc_in.default_value = JACKET_COLOR
    # Rauheit in einen realistischen Nylon-Bereich bringen
    r_in = b.inputs["Roughness"]
    if r_in.is_linked:
        src = r_in.links[0].from_socket
        mr = N.new("ShaderNodeMapRange")
        mr.label = "Nylon Roughness Range"
        mr.inputs["To Min"].default_value = 0.52
        mr.inputs["To Max"].default_value = 0.80
        L.new(src, mr.inputs["Value"])
        L.new(mr.outputs["Result"], r_in)
    else:
        r_in.default_value = 0.62
    b.inputs["Specular IOR Level"].default_value = 0.45
    b.inputs["Sheen Weight"].default_value = 0.35
    b.inputs["Sheen Roughness"].default_value = 0.45
    # feine Gewebestruktur (Mikro-Bump)
    tc = N.new("ShaderNodeTexCoord")
    noise = N.new("ShaderNodeTexNoise")
    noise.label = "Fabric Micro Texture"
    noise.inputs["Scale"].default_value = 1400.0
    noise.inputs["Detail"].default_value = 2.0
    L.new(tc.outputs["Object"], noise.inputs["Vector"])
    bump = N.new("ShaderNodeBump")
    bump.label = "Fabric Bump"
    bump.inputs["Strength"].default_value = 0.035
    bump.inputs["Distance"].default_value = 0.0004
    L.new(noise.outputs["Fac"], bump.inputs["Height"])
    n_in = b.inputs["Normal"]
    if n_in.is_linked:
        L.new(n_in.links[0].from_socket, bump.inputs["Normal"])
    L.new(bump.outputs["Normal"], n_in)

# ---------------------------------------------------------------- logo decals
LOGO_PLACEMENTS = {
    # name: (location relative to jacket, rotation, width in m)
    "chest_left":  (lambda: Vector((mn.x + (mx.x - mn.x) * 0.64, mn.y, mn.z + (mx.z - mn.z) * 0.53)),
                    (math.radians(90), 0, 0), 0.085),
    "chest_center": (lambda: Vector((CENTER.x, mn.y, mn.z + (mx.z - mn.z) * 0.50)),
                     (math.radians(90), 0, 0), 0.16),
    "back":        (lambda: Vector((CENTER.x, mx.y, mn.z + (mx.z - mn.z) * 0.45)),
                    (math.radians(90), 0, math.radians(180)), 0.28),
    # vorne unten seitlich auf Bauch/Rippen (zwischen Reissverschluss und Seitentasche)
    "ribs_left":   (lambda: Vector((CENTER.x + 0.075, mn.y, mn.z + (mx.z - mn.z) * 0.24)),
                    (math.radians(90), 0, 0), 0.11),
    "ribs_right":  (lambda: Vector((CENTER.x - 0.075, mn.y, mn.z + (mx.z - mn.z) * 0.24)),
                    (math.radians(90), 0, 0), 0.11),
    # hinten klein unter dem Nacken
    "nape":        (lambda: Vector((CENTER.x, mx.y, mn.z + (mx.z - mn.z) * 0.655)),
                    (math.radians(90), 0, math.radians(180)), 0.05),
    # vorne mittig auf dem Kapuzenrand ueber der Gesichtsoeffnung
    "hood_front":  (lambda: Vector((CENTER.x, mn.y, mn.z + (mx.z - mn.z) * 0.9275)),
                    (math.radians(90), 0, 0), 0.075),
    # seitlich an der Kapuze (rechte Seite des Traegers), Schrift laeuft nach vorne
    "hood_side_right": (lambda: Vector((CENTER.x - 0.4, CENTER.y - 0.01, mn.z + (mx.z - mn.z) * 0.82)),
                        (math.radians(90), 0, math.radians(-90)), 0.09),
}
# Rundum-Logos: zylindrisch um den Koerper gewickelt (laufen z.B. von vorne ueber die Seite nach hinten).
# name: (Mittelpunkt als Winkel in Grad: 0 = vorne, 90 = rechte Seite des Traegers, 180 = hinten,
#        Hoehe als Anteil der Jackenhoehe, Standard-Breite in m)
WRAP_PLACEMENTS = {
    # name: (Mittelpunkt-Winkel, Hoehe-Anteil, Breite, max. Winkel oder None, max. Abstand zur Koerperachse in m)
    "wrap_side_right":      (90.0, 0.31, 0.16, None, 0.195),
    # seitlich: laeuft unter den Aermel und schaut vorne heraus (nur auf dem Koerper, nicht auf dem Aermel)
    "wrap_frontside_right": (66.0, 0.31, 0.15, None, 0.178),
    # weiter vorne, endet vor dem Aermel (komplett sichtbar)
    "wrap_front_right":     (45.0, 0.31, 0.15, 71.0, 0.195),
}


def compute_body_mask(z_lo, z_hi):
    """Vertex-Attribut 'body_mask': 1 = Jackenkoerper, 0 = Aermel.
    Ein Punkt gehoert zum Aermel, wenn zwischen ihm und der Koerperachse mehr als eine Stofflage liegt
    (Futter + Koerper-Aussenseite). Nur im Hoehenband des Logos berechnet, sonst 1."""
    from mathutils.bvhtree import BVHTree
    me = jacket.data
    attr = me.attributes.get("body_mask")
    if attr is None:
        attr = me.attributes.new("body_mask", "FLOAT", "POINT")
        vals = [1.0] * len(me.vertices)
    else:
        vals = [d.value for d in attr.data]
    bvh = BVHTree.FromObject(jacket, bpy.context.evaluated_depsgraph_get())
    n_sleeve = 0
    for v in me.vertices:
        co = v.co
        if co.z < z_lo or co.z > z_hi:
            continue
        axis_pt = Vector((CENTER.x, CENTER.y, co.z))
        dv = co - axis_pt
        dist = dv.length
        if dist < 1e-4:
            continue
        d = dv / dist
        o, remaining, layers = axis_pt, dist - 0.004, 0
        while remaining > 0:
            loc, _n, _i, t = bvh.ray_cast(o, d, remaining)
            if loc is None:
                break
            layers += 1
            o = loc + d * 0.002
            remaining -= t + 0.002
        is_body = layers <= 1
        vals[v.index] = 1.0 if is_body else 0.0
        n_sleeve += 0 if is_body else 1
    attr.data.foreach_set("value", vals)
    print("BODY_MASK band", round(z_lo, 3), round(z_hi, 3), "sleeve verts", n_sleeve)
WRAP_RADIUS = 0.17      # Referenzradius fuer die Bogenlaenge (Rumpf ~17 cm)
WRAP_MAX_R = 0.195      # nur Rumpf, nicht die Aermel (die haengen bei ~26-29 cm)
LOGO_SIZE = [float(v) for v in A.get("logo_size", "").split(",") if v]   # Breite in m je Logo
LOGO_ROT = [float(v) for v in A.get("logo_rot", "").split(",") if v]     # Drehung in der Ebene (Grad)
LOGO_DX = [float(v) for v in A.get("logo_dx", "").split(",") if v]       # Verschiebung seitlich (m)
LOGO_DZ = [float(v) for v in A.get("logo_dz", "").split(",") if v]       # Verschiebung hoch/runter (m)


def snap_to_surface(loc, rot):
    """Schiebt den Projektor entlang seiner Blickrichtung genau auf die Jackenoberflaeche."""
    from mathutils import Euler
    fwd = Euler(rot).to_matrix() @ Vector((0, 0, -1))   # Projektionsrichtung (in die Jacke)
    origin = loc - fwd * 0.5
    dg = bpy.context.evaluated_depsgraph_get()
    hit, p, *_ = scene.ray_cast(dg, origin, fwd)
    return p if hit else loc


def add_decal(name, img, placement, finish, width=None, inplane_deg=0.0, dx=0.0, dz=0.0):
    w, h = img.size
    m = max(w, h)
    wrap = placement in WRAP_PLACEMENTS
    emp = bpy.data.objects.new(name, None)
    scene.collection.objects.link(emp)
    if wrap:
        # Empty sitzt auf der Koerperachse; Drehen um Z verschiebt das Logo um den Koerper,
        # Hoch/Runter-Schieben verschiebt es in der Hoehe. Breite: Node "Wrap Width".
        phi0, zf, default_width, max_angle, max_r = WRAP_PLACEMENTS[placement]
        width = width or default_width
        emp.empty_display_type = "SINGLE_ARROW"
        emp.empty_display_size = 0.25
        emp.location = Vector((CENTER.x, CENTER.y, mn.z + (mx.z - mn.z) * zf + dz))
        emp.rotation_euler = (0.0, 0.0, -math.radians(phi0))
        emp["wrap"] = True
        emp["wrap_phi0"] = phi0
        half_h = width * h / w / 2 + 0.03
        compute_body_mask(emp.location.z - half_h, emp.location.z + half_h)
    else:
        loc_fn, rot, default_width = LOGO_PLACEMENTS[placement]
        width = width or default_width
        loc = snap_to_surface(loc_fn() + Vector((dx, 0.0, dz)), rot)
        # Drehung in der Logo-Ebene = Euler-Y (Projektionsachse liegt nach X=90 Grad auf der Welt-Y-Achse)
        rot = (rot[0], math.radians(inplane_deg), rot[2])
        emp.empty_display_type = "IMAGE"
        emp.data = img
        emp.empty_display_size = 1.0
        emp.empty_image_offset = (-0.5, -0.5)
        emp.empty_image_depth = "FRONT"
        emp.use_empty_image_alpha = True
        emp.color[3] = 0.55
        emp.location = loc
        emp.rotation_euler = rot
        emp.scale = (width, width, width)
    for mat in mats:
        N, L = mat.node_tree.nodes, mat.node_tree.links
        b = principled(mat)
        frame = N.new("NodeFrame")
        frame.label = f"{name} ({finish})"

        def new(t, **kw):
            n = N.new(t)
            n.parent = frame
            for k, v in kw.items():
                setattr(n, k, v)
            return n

        tc = new("ShaderNodeTexCoord", object=emp)
        tex = new("ShaderNodeTexImage", image=img, extension="CLIP", interpolation="Cubic")
        tex.label = "LOGO IMAGE (hier tauschen)"
        sep = new("ShaderNodeSeparateXYZ")
        L.new(tc.outputs["Object"], sep.inputs["Vector"])

        def math_node(op, a, b=None, label=None):
            n = new("ShaderNodeMath", operation=op)
            if label:
                n.label = label
            for i, v in enumerate((a, b)):
                if v is None:
                    continue
                if isinstance(v, (int, float)):
                    n.inputs[i].default_value = v
                else:
                    L.new(v, n.inputs[i])
            return n.outputs[0]

        if wrap:
            # Zylinder-Koordinaten um die Koerperachse: Winkel -> u (Bogenlaenge), Hoehe -> v
            x, y, z = sep.outputs["X"], sep.outputs["Y"], sep.outputs["Z"]
            phi = math_node("ARCTAN2", math_node("MULTIPLY", x, -1.0), math_node("MULTIPLY", y, -1.0))
            wv = new("ShaderNodeValue")
            wv.label = "Wrap Width (m)"
            wv.outputs[0].default_value = width
            s = math_node("MULTIPLY", phi, -WRAP_RADIUS)
            u = math_node("ADD", math_node("DIVIDE", s, wv.outputs[0]), 0.5)
            hgt = math_node("MULTIPLY", wv.outputs[0], h / w)
            v = math_node("ADD", math_node("DIVIDE", z, hgt), 0.5)
            cmb = new("ShaderNodeCombineXYZ")
            L.new(u, cmb.inputs["X"])
            L.new(v, cmb.inputs["Y"])
            L.new(cmb.outputs["Vector"], tex.inputs["Vector"])
            r = math_node("SQRT", math_node("ADD", math_node("MULTIPLY", x, x), math_node("MULTIPLY", y, y)))
            depth_out = math_node("LESS_THAN", r, max_r, label="Only torso (not sleeves)")
            body = new("ShaderNodeAttribute", attribute_type="GEOMETRY", attribute_name="body_mask")
            body.label = "Body mask (no sleeves)"
            depth_out = math_node("MULTIPLY", depth_out, math_node("GREATER_THAN", body.outputs["Fac"], 0.5))
            if max_angle is not None:
                # harte Grenze vor dem Aermelansatz (Winkel relativ zur Logo-Mitte)
                cut = math_node("LESS_THAN", phi, math.radians(max_angle - phi0), label="Sleeve cutoff angle")
                depth_out = math_node("MULTIPLY", depth_out, cut)
        else:
            mp = new("ShaderNodeMapping")
            mp.inputs["Location"].default_value = (0.5, 0.5, 0.0)
            mp.inputs["Scale"].default_value = (m / w, m / h, 1.0)
            L.new(tc.outputs["Object"], mp.inputs["Vector"])
            L.new(mp.outputs["Vector"], tex.inputs["Vector"])
            # nur auf der Oberflaeche nahe dem Projektor (nicht durch die Jacke hindurch)
            ab = math_node("ABSOLUTE", sep.outputs["Z"])
            depth_out = math_node("LESS_THAN", ab, 0.6, label="Projection Depth")
            # ... und nur auf Flaechen, die dem Logo zugewandt sind (kein Durchscheinen aufs Innenfutter).
            # Blickrichtung des Logo-Empties per Treiber (folgt, wenn man das Logo verschiebt/dreht).
            facing_dir = new("ShaderNodeCombineXYZ")
            facing_dir.label = "Logo direction (driver)"
            exprs = {"X": "cos(rx)*sin(ry)*cos(rz)+sin(rx)*sin(rz)",
                     "Y": "cos(rx)*sin(ry)*sin(rz)-sin(rx)*cos(rz)",
                     "Z": "cos(rx)*cos(ry)"}
            for axis, expr in exprs.items():
                fc = facing_dir.inputs[axis].driver_add("default_value")
                drv = fc.driver
                drv.type = "SCRIPTED"
                for vname, ch in (("rx", "ROT_X"), ("ry", "ROT_Y"), ("rz", "ROT_Z")):
                    var = drv.variables.new()
                    var.name, var.type = vname, "TRANSFORMS"
                    var.targets[0].id = emp
                    var.targets[0].transform_type = ch
                    var.targets[0].transform_space = "TRANSFORM_SPACE"
                drv.expression = expr
            geo = new("ShaderNodeNewGeometry")
            vt = new("ShaderNodeVectorTransform", vector_type="NORMAL", convert_from="WORLD", convert_to="OBJECT")
            L.new(geo.outputs["Normal"], vt.inputs["Vector"])
            dot = new("ShaderNodeVectorMath", operation="DOT_PRODUCT")
            L.new(vt.outputs["Vector"], dot.inputs[0])
            L.new(facing_dir.outputs["Vector"], dot.inputs[1])
            facing = math_node("GREATER_THAN", dot.outputs["Value"], 0.05, label="Only surfaces facing the logo")
            depth_out = math_node("MULTIPLY", depth_out, facing)

        m1 = new("ShaderNodeMath", operation="MULTIPLY")
        L.new(tex.outputs["Alpha"], m1.inputs[0])
        L.new(depth_out, m1.inputs[1])
        opac = new("ShaderNodeMath", operation="MULTIPLY")
        opac.label = "Logo Opacity"
        opac.inputs[1].default_value = 1.0
        L.new(m1.outputs[0], opac.inputs[0])
        mask = opac.outputs[0]

        # Farbe: Logo bekommt die Stoff-Schattierung (Naehte, Falten) mit
        base_src = ensure_source(mat, b.inputs["Base Color"], "ShaderNodeRGB",
                                 lambda n: setattr(n.outputs[0], "default_value", (0.9, 0.9, 0.88, 1)))
        shade = new("ShaderNodeMix", data_type="RGBA", blend_type="MULTIPLY")
        shade.label = "Fabric shading on logo"
        sock(shade.inputs, "Factor_Float").default_value = 0.55 if finish == "print" else 0.35
        L.new(tex.outputs["Color"], sock(shade.inputs, "A_Color"))
        L.new(base_src, sock(shade.inputs, "B_Color"))
        col = new("ShaderNodeMix", data_type="RGBA", blend_type="MIX")
        L.new(mask, sock(col.inputs, "Factor_Float"))
        L.new(base_src, sock(col.inputs, "A_Color"))
        L.new(sock(shade.outputs, "Result_Color"), sock(col.inputs, "B_Color"))
        L.new(sock(col.outputs, "Result_Color"), b.inputs["Base Color"])

        # Rauheit: Siebdruck/Silikon leicht glaenzend, Stickerei matt
        r_src = ensure_source(mat, b.inputs["Roughness"], "ShaderNodeValue",
                              lambda n: setattr(n.outputs[0], "default_value", 0.62))
        rmix = new("ShaderNodeMix", data_type="FLOAT")
        L.new(mask, sock(rmix.inputs, "Factor_Float"))
        L.new(r_src, sock(rmix.inputs, "A_Float"))
        sock(rmix.inputs, "B_Float").default_value = {"print": 0.38, "embroidery": 0.85, "patch": 0.7}[finish]
        L.new(sock(rmix.outputs, "Result_Float"), b.inputs["Roughness"])

        # Hoehe: Druckkante bzw. Stickfaeden
        height = mask
        if finish == "embroidery":
            wave = new("ShaderNodeTexWave", wave_type="BANDS", bands_direction="DIAGONAL")
            wave.inputs["Scale"].default_value = 260.0
            wave.inputs["Distortion"].default_value = 1.5
            L.new(tc.outputs["Object"], wave.inputs["Vector"])
            thr = new("ShaderNodeMath", operation="MULTIPLY_ADD")
            L.new(wave.outputs["Fac"], thr.inputs[0])
            thr.inputs[1].default_value = 0.35
            thr.inputs[2].default_value = 0.65
            hm = new("ShaderNodeMath", operation="MULTIPLY")
            L.new(mask, hm.inputs[0])
            L.new(thr.outputs[0], hm.inputs[1])
            height = hm.outputs[0]
        bump = new("ShaderNodeBump")
        bump.label = "Logo Relief"
        bump.inputs["Strength"].default_value = {"print": 0.25, "embroidery": 0.6, "patch": 0.8}[finish]
        bump.inputs["Distance"].default_value = {"print": 0.0003, "embroidery": 0.0012, "patch": 0.0015}[finish]
        L.new(height, bump.inputs["Height"])
        n_in = b.inputs["Normal"]
        if n_in.is_linked:
            L.new(n_in.links[0].from_socket, bump.inputs["Normal"])
        L.new(bump.outputs["Normal"], n_in)
    return emp


logo_empties = []
for i, path in enumerate(LOGOS):
    img = bpy.data.images.load(os.path.abspath(path))
    img.name = os.path.basename(path)
    pos = LOGO_POS[i] if i < len(LOGO_POS) else LOGO_POS[-1]
    size = LOGO_SIZE[i] if i < len(LOGO_SIZE) else None
    rotd = LOGO_ROT[i] if i < len(LOGO_ROT) else 0.0
    dx = LOGO_DX[i] if i < len(LOGO_DX) else 0.0
    dz = LOGO_DZ[i] if i < len(LOGO_DZ) else 0.0
    logo_empties.append(add_decal(f"Logo_{pos}", img, pos, FINISH, size, rotd, dx, dz))

# ---------------------------------------------------------------- studio
world = bpy.data.worlds.new("Studio")
scene.world = world
world.use_nodes = True
bg = world.node_tree.nodes["Background"]
bg.inputs["Color"].default_value = (0.8, 0.8, 0.8, 1)
bg.inputs["Strength"].default_value = 0.08

# Hohlkehle (Seamless-Backdrop)
bm = bmesh.new()
prof = [(-6.0, 0.0), (-3.0, 0.0), (0.0, 0.0), (1.2, 0.0)]
R, cy = 1.4, 1.2
for i in range(1, 17):
    a = -math.pi / 2 + (math.pi / 2) * i / 16
    prof.append((cy + R * math.cos(a), R + R * math.sin(a)))
prof += [(cy + R, 3.0), (cy + R, 6.0)]
rows = []
for x in (-6.0, 6.0):
    rows.append([bm.verts.new((x, y, z)) for (y, z) in prof])
for i in range(len(prof) - 1):
    bm.faces.new((rows[0][i], rows[1][i], rows[1][i + 1], rows[0][i + 1]))
me = bpy.data.meshes.new("Backdrop")
bm.to_mesh(me)
bm.free()
for p in me.polygons:
    p.use_smooth = True
backdrop = bpy.data.objects.new("Backdrop", me)
scene.collection.objects.link(backdrop)
bmat = bpy.data.materials.new("Backdrop_Paper")
bmat.use_nodes = True
pb = bmat.node_tree.nodes["Principled BSDF"]
pb.inputs["Base Color"].default_value = tuple(float(v) for v in A.get("backdrop", "0.30,0.30,0.295").split(",")) + (1,)
pb.inputs["Roughness"].default_value = 0.95
me.materials.append(bmat)

target = bpy.data.objects.new("Look_Target", None)
scene.collection.objects.link(target)
target.location = CENTER


def aim(obj, at):
    obj.rotation_euler = (at - obj.location).to_track_quat("-Z", "Y").to_euler()


def area(name, loc, size, size_y, power, color=(1, 1, 1)):
    d = bpy.data.lights.new(name, "AREA")
    d.shape = "RECTANGLE"
    d.size, d.size_y, d.energy, d.color = size, size_y, power, color
    o = bpy.data.objects.new(name, d)
    scene.collection.objects.link(o)
    o.location = loc
    aim(o, CENTER)
    return o


area("Key_Softbox", CENTER + Vector((-2.0, -1.1, 1.0)), 0.9, 1.3, 170, (1.0, 0.96, 0.92))
area("Fill_Softbox", CENTER + Vector((2.2, -1.6, 0.1)), 2.0, 2.0, 18, (0.93, 0.96, 1.0))
area("Top_Softbox", CENTER + Vector((0.4, 0.3, 2.0)), 1.0, 1.0, 45)
area("Rim_Left", CENTER + Vector((-1.2, 1.2, 0.7)), 0.3, 1.8, 110)
area("Rim_Right", CENTER + Vector((1.2, 1.2, 0.7)), 0.3, 1.8, 90)
area("Backdrop_Glow", Vector((0.0, 1.6, 2.6)), 3.0, 0.6, 60)  # weicher Verlauf auf der Hohlkehle

# Drehteller: Jacke + Logos drehen sich, Kamera/Licht bleiben -> jede Ansicht im gleichen Studio-Look.
# Zusaetzlich 360-Grad-Animation (Frame 1-120) fuer ein Turntable-Video.
turntable = bpy.data.objects.new("Turntable", None)
scene.collection.objects.link(turntable)
turntable.location = Vector((CENTER.x, CENTER.y, 0.0))
turntable.empty_display_type = "CIRCLE"
turntable.empty_display_size = 0.45
bpy.context.view_layer.update()  # matrix_world neuer Objekte erst nach Update gueltig
for o in [jacket] + logo_empties:
    mw = o.matrix_world.copy()
    o.parent = turntable
    o.matrix_parent_inverse = turntable.matrix_world.inverted()
    o.matrix_world = mw
bpy.context.view_layer.update()
scene.frame_start, scene.frame_end = 1, 120
scene.render.fps = 30
turntable.rotation_euler.z = 0.0
turntable.keyframe_insert("rotation_euler", index=2, frame=1)
turntable.rotation_euler.z = math.radians(360)
turntable.keyframe_insert("rotation_euler", index=2, frame=121)
for fc in turntable.animation_data.action.fcurves:
    for kp in fc.keyframe_points:
        kp.interpolation = "LINEAR"
scene.frame_set(1)

# Kameras
cams = {}


def camera(name, az_deg, el_deg, dist, lens, at=None):
    at = at if at is not None else CENTER
    d = bpy.data.cameras.new(name)
    d.lens = lens
    d.sensor_width = 36
    o = bpy.data.objects.new(name, d)
    scene.collection.objects.link(o)
    az, el = math.radians(az_deg), math.radians(el_deg)
    o.location = at + Vector((math.sin(az) * math.cos(el), -math.cos(az) * math.cos(el), math.sin(el))) * dist
    c = o.constraints.new("TRACK_TO")
    c.target = target if at is CENTER else None
    if c.target is None:
        o.constraints.remove(c)
        aim(o, at)
    else:
        c.track_axis, c.up_axis = "TRACK_NEGATIVE_Z", "UP_Y"
    cams[name] = o
    return o


camera("Cam_Front", 0, 4, 2.3, 70)
camera("Cam_ThreeQuarter", -35, 8, 2.3, 70)
# Halbnah auf den Rumpf (fuer Rundum-Logos zusammen mit gedrehtem Drehteller: "torso@90" usw.)
camera("Cam_Torso", 0, 3, 1.35, 60, at=Vector((CENTER.x, CENTER.y, mn.z + (mx.z - mn.z) * 0.36)))
for i, e in enumerate(logo_empties):
    # Nahaufnahme je Logo (Rueckseiten-Logos werden ueber den Drehteller nach vorne gedreht)
    if e.get("wrap"):
        continue  # Rundum-Logos: Ansichten ueber "torso@<Grad>"
    p = e.matrix_world.translation.copy()
    nrm = (e.matrix_world.to_3x3() @ Vector((0, 0, 1))).normalized()  # zeigt aus der Jacke heraus
    on_back = nrm.y > 0.5
    if on_back:
        p = Vector((2 * CENTER.x - p.x, 2 * CENTER.y - p.y, p.z))  # Position nach 180-Grad-Drehung
        nrm = Vector((-nrm.x, -nrm.y, nrm.z))
    az = math.degrees(math.atan2(nrm.x, -nrm.y))
    if abs(az) < 30:
        az += 14 if p.x > CENTER.x else -14
    else:
        az -= math.copysign(14, az)   # leicht von vorne
    cam_name = "Cam_Logo_Closeup" if i == 0 else f"Cam_{e.name}"
    camera(cam_name, az, 8, 0.75 if e.scale.x > 0.07 else 0.55, 85, at=p)
    cams[cam_name]["on_back"] = on_back
scene.camera = cams["Cam_ThreeQuarter"]

# ---------------------------------------------------------------- render settings
scene.render.engine = "CYCLES"
scene.cycles.device = "CPU"
scene.cycles.samples = SAMPLES
scene.cycles.use_adaptive_sampling = True
scene.cycles.adaptive_threshold = 0.02
scene.cycles.max_bounces = 8
scene.cycles.use_denoising = True
try:
    scene.cycles.denoiser = "OPENIMAGEDENOISE"
except TypeError:
    pass  # Build ohne OIDN: Blender waehlt beim Oeffnen auf deinem Rechner automatisch OIDN
try:
    import _cycles
    HAS_OIDN = bool(getattr(_cycles, "with_openimagedenoise", False))
except Exception:
    HAS_OIDN = False
scene.render.resolution_x, scene.render.resolution_y = RES_X, RES_Y
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = "PNG"
scene.render.image_settings.color_mode = "RGB"
try:
    scene.view_settings.view_transform = "AgX"
    scene.view_settings.look = "AgX - Medium High Contrast"
except Exception:
    scene.view_settings.view_transform = "Filmic"
scene.view_settings.exposure = float(A.get("exposure", "0.0"))

# ---------------------------------------------------------------- save + render
bpy.ops.file.pack_all()
bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(OUT), compress=True)
print("SAVED", OUT)

os.makedirs(RENDER_DIR, exist_ok=True)
if RENDER and not HAS_OIDN:
    # Dieser Blender-Build hat keinen Denoiser: ohne Denoise, dafuer mehr Samples rendern.
    # (Die gespeicherte .blend behaelt OpenImageDenoise aktiviert.)
    print("WARN: kein OpenImageDenoise -> rendere ohne Denoiser")
    scene.cycles.use_denoising = False
    scene.cycles.samples = int(A.get("nodenoise_samples", str(SAMPLES * 3)))
    scene.cycles.adaptive_threshold = 0.008
SHOTS = {  # key: (camera, turntable-Frame)  Frame 1 = 0 Grad, 31 = 90 Grad, 61 = 180 Grad
    "front": ("Cam_Front", 1),
    "threequarter": ("Cam_ThreeQuarter", 1),
    "side": ("Cam_Front", 31),
    "back": ("Cam_Front", 61),
    "backquarter": ("Cam_ThreeQuarter", 61),
}
for cname, cobj in cams.items():
    if cname.startswith("Cam_Logo"):
        key = "logo" if cname == "Cam_Logo_Closeup" else cname[len("Cam_"):].lower()
        SHOTS[key] = (cname, 61 if cobj.get("on_back") else 1)
print("SHOTS", sorted(SHOTS))
for key in RENDER:
    if key == "turntable":
        scene.camera = cams["Cam_Front"]
        scene.render.image_settings.file_format = "FFMPEG"
        scene.render.ffmpeg.format = "MPEG4"
        scene.render.ffmpeg.codec = "H264"
        scene.render.ffmpeg.constant_rate_factor = "HIGH"
        scene.render.filepath = os.path.join(RENDER_DIR, f"turntable{TAG}.mp4")
        bpy.ops.render.render(animation=True)
        scene.render.image_settings.file_format = "PNG"
        print("RENDERED", scene.render.filepath)
        continue
    if "@" in key:   # z.B. "torso@90": Kamera + Drehteller-Winkel in Grad (3 Grad pro Frame)
        base, deg = key.split("@")
        cam = SHOTS.get(base, (f"Cam_{base.capitalize()}", 1))[0]
        f = 1 + float(deg) / 3.0
        if cam not in cams:
            continue
        scene.frame_set(int(f), subframe=f - int(f))
    else:
        cam, frame = SHOTS.get(key, (None, 1))
        if cam not in cams:
            continue
        scene.frame_set(frame)
    scene.camera = cams[cam]
    key = key.replace("@", "_")
    scene.render.filepath = os.path.join(RENDER_DIR, f"{key}{TAG}.png")
    bpy.ops.render.render(write_still=True)
    print("RENDERED", scene.render.filepath)
scene.frame_set(1)
