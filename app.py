import os
os.environ["U2NET_HOME"] = "/tmp"

import os
import io
import uuid
import glob
import time
from flask import Flask, request, jsonify, render_template, send_file, abort

app = Flask(__name__)

MAX_FILE_SIZE = 10 * 1024 * 1024
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}
UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

app.config["MAX_CONTENT_LENGTH"] = MAX_FILE_SIZE
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def cleanup_old_uploads(max_age_seconds=3600):
    now = time.time()
    for filepath in glob.glob(os.path.join(UPLOAD_FOLDER, "*.png")):
        try:
            if now - os.path.getmtime(filepath) > max_age_seconds:
                os.remove(filepath)
        except OSError:
            pass


# ── rembg session (lazy-loaded, reused across requests) ──────────────────────
_rembg_session = None

def get_rembg_session():
    """
    Returns a persistent rembg session using the isnet-general-use model.
    Initialised on first call; the model is downloaded automatically if needed.
    Reusing the session avoids reloading the ONNX model on every request.
    """
    global _rembg_session
    if _rembg_session is None:
        import rembg
        _rembg_session = rembg.new_session("isnet-general-use")
    return _rembg_session


# ── Alpha post-processing ─────────────────────────────────────────────────────
def clean_alpha(image, low_cut=12):
    """
    Cleans rembg output for crisp, professional edges.

    Steps:
    1. Kill fringe: any alpha pixel below `low_cut` → 0  (removes gray halos
       and semi-transparent background remnants around logos / hard objects).
    2. Sharpen the edge with a gentle UnsharpMask on the alpha channel only,
       giving crisper boundaries without damaging the RGB colours.
    """
    from PIL import Image, ImageFilter

    if image.mode != "RGBA":
        image = image.convert("RGBA")

    r, g, b, a = image.split()

    # Step 1 — kill near-transparent fringe pixels
    alpha_data = [0 if v < low_cut else v for v in a.getdata()]
    a.putdata(alpha_data)

    # Step 2 — sharpen the alpha edge
    # radius=0.6 keeps it tight; percent=180 boosts contrast at the boundary;
    # threshold=2 avoids sharpening flat interior regions.
    a = a.filter(ImageFilter.UnsharpMask(radius=0.6, percent=180, threshold=2))

    image.putalpha(a)
    return image


def generate_shadow(image):
    """
    Adds a realistic soft product-photography drop shadow.

    Algorithm:
    1. Squash the subject's alpha channel to a thin footprint (matches the
       actual silhouette, not just an ellipse).
    2. Stretch it slightly wider than the subject.
    3. Apply heavy Gaussian blur for smooth, natural edges.
    4. Reduce opacity so shadow is translucent.
    5. Composite under the original RGBA image on an expanded canvas.
    """
    from PIL import Image, ImageFilter

    if image.mode != "RGBA":
        image = image.convert("RGBA")

    w, h = image.size
    _, _, _, alpha = image.split()

    # Bounding box of non-transparent pixels
    bbox = alpha.getbbox()
    if not bbox:
        return image

    left, top, right, bottom = bbox
    subject_w = right - left
    subject_h = bottom - top
    subject_cx = (left + right) // 2

    # --- Shadow shape params ---
    h_stretch    = 1.10                                # shadow slightly wider than object
    shadow_h     = max(24, int(subject_h * 0.10))      # shadow height = 10% of object
    shadow_w     = int(subject_w * h_stretch)
    blur_radius  = max(20, int(subject_w * 0.09))      # proportional heavy blur
    opacity      = 0.55                                # natural, not too dark
    y_offset     = max(4, int(subject_h * 0.012))      # tiny downward nudge

    # Squash the subject's alpha to the shadow footprint size.
    # This preserves the silhouette shape — narrower objects get narrow shadows,
    # wider objects get wider shadows. Far better than a plain ellipse.
    subject_alpha_crop = alpha.crop((left, top, right, bottom))
    shadow_mask = subject_alpha_crop.resize((shadow_w, shadow_h), Image.LANCZOS)

    # Blur — double-pass for extra softness
    shadow_mask = shadow_mask.filter(ImageFilter.GaussianBlur(radius=blur_radius))
    shadow_mask = shadow_mask.filter(ImageFilter.GaussianBlur(radius=blur_radius // 2))

    # Apply opacity by scaling pixel values
    shadow_data = [int(p * opacity) for p in shadow_mask.getdata()]
    shadow_mask.putdata(shadow_data)

    # Build shadow RGBA: near-black with the blurred mask as alpha
    shadow_img = Image.new("RGBA", (shadow_w, shadow_h), (10, 10, 18, 0))
    shadow_img.putalpha(shadow_mask)

    # Expand canvas downward to hold shadow below the object
    extra_h = shadow_h + blur_radius + y_offset + 20
    canvas = Image.new("RGBA", (w, h + extra_h), (0, 0, 0, 0))

    # Center shadow horizontally on the subject
    sx = subject_cx - shadow_w // 2
    sy = bottom + y_offset

    # Paste shadow first (below), then original image on top
    canvas.paste(shadow_img, (sx, sy), shadow_img)
    canvas.paste(image, (0, 0), image)

    return canvas


@app.after_request
def set_headers(response):
    response.headers.pop("X-Frame-Options", None)
    response.headers["Content-Security-Policy"] = "frame-ancestors *"
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/remove-bg", methods=["POST"])
def remove_background():
    if "image" not in request.files:
        return jsonify({"error": "No image file provided"}), 400

    file = request.files["image"]

    if not file or file.filename == "":
        return jsonify({"error": "No file selected"}), 400

    if not allowed_file(file.filename):
        return jsonify({"error": "Invalid file type. Please upload a JPG, PNG, or WEBP image"}), 400

    add_shadow = request.form.get("shadow", "0") == "1"

    try:
        from PIL import Image
        import rembg

        file.seek(0)
        image_data = file.read()

        if len(image_data) == 0:
            return jsonify({"error": "Uploaded file is empty"}), 400

        input_image = Image.open(io.BytesIO(image_data))

        if input_image.mode not in ("RGB", "RGBA"):
            input_image = input_image.convert("RGB")

        # Use isnet-general-use model via persistent session for cleaner edges
        session = get_rembg_session()
        output_image = rembg.remove(input_image, session=session)

        # Post-process: remove fringe pixels and sharpen edge boundary
        output_image = clean_alpha(output_image)

        if add_shadow:
            output_image = generate_shadow(output_image)

        output_buffer = io.BytesIO()
        output_image.save(output_buffer, format="PNG")
        output_buffer.seek(0)

        result_filename = f"{uuid.uuid4().hex}.png"
        result_path = os.path.join(UPLOAD_FOLDER, result_filename)

        with open(result_path, "wb") as f:
            f.write(output_buffer.getvalue())

        cleanup_old_uploads()

        return jsonify({"result_id": result_filename, "shadow": add_shadow})

    except Exception as e:
        return jsonify({"error": f"Failed to process image: {str(e)}"}), 500


@app.route("/download/<result_id>")
def download(result_id):
    if ".." in result_id or "/" in result_id or "\\" in result_id:
        abort(400)
    result_path = os.path.join(UPLOAD_FOLDER, result_id)
    if not os.path.exists(result_path):
        abort(404)
    return send_file(
        result_path,
        mimetype="image/png",
        as_attachment=True,
        download_name="bgly-result.png"
    )


@app.route("/preview/<result_id>")
def preview(result_id):
    if ".." in result_id or "/" in result_id or "\\" in result_id:
        abort(400)
    result_path = os.path.join(UPLOAD_FOLDER, result_id)
    if not os.path.exists(result_path):
        abort(404)
    return send_file(result_path, mimetype="image/png")


@app.errorhandler(413)
def file_too_large(e):
    return jsonify({"error": "File too large. Maximum size is 10MB"}), 413


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
