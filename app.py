import io
import fitz  # PyMuPDF
import streamlit as st
import os
import shutil
import zipfile
import hashlib
import re
import streamlit.components.v1 as components
from PIL import ImageFile

# Prevent Pillow from crashing on incomplete/truncated PDF images
ImageFile.LOAD_TRUNCATED_IMAGES = True

# --- CRITICAL FIX ---
fitz.TOOLS.mupdf_display_errors(False)

st.set_page_config(page_title="PDF Image Watermark Remover", layout="centered")
st.title("🖼️ PDF Image Watermark Remover")
st.write("Upload your PDF or ZIP files. The app will extract all images, sort them by frequency, and let you select the watermarks to permanently remove.")

# --- AUTOMATED PWA INJECTION LOGIC ---
try:
    streamlit_static_path = os.path.join(os.path.dirname(st.__file__), "static")
    manifest_dest = os.path.join(streamlit_static_path, "manifest.json")
    sw_dest = os.path.join(streamlit_static_path, "sw.js")

    if not os.path.exists(manifest_dest):
        shutil.copy("pwa/manifest.json", manifest_dest)
    if not os.path.exists(sw_dest):
        shutil.copy("pwa/sw.js", sw_dest)
except Exception:
    pass

pwa_html = """
<script>
    var link = window.parent.document.createElement('link');
    link.rel = 'manifest';
    link.href = './manifest.json';
    window.parent.document.getElementsByTagName('head')[0].appendChild(link);

    if ('serviceWorker' in navigator) {
        navigator.serviceWorker.register('./sw.js')
    }
</script>
"""
components.html(pwa_html, height=0, width=0)

# --- Core Logic Functions ---
def get_image_hash(image_bytes):
    return hashlib.md5(image_bytes).hexdigest()

@st.cache_data
def scan_for_images(file_data):
    """Scans PDFs to find unique images and count their frequency."""
    image_freq = {}
    image_bytes_map = {}
    pdf_files = []

    for item in file_data:
        if item["name"].lower().endswith('.zip'):
            with zipfile.ZipFile(io.BytesIO(item["bytes"])) as z:
                for f in z.namelist():
                    if f.lower().endswith('.pdf'):
                        pdf_files.append((f"{item['name']}/{f}", z.read(f)))
        elif item["name"].lower().endswith('.pdf'):
            pdf_files.append((item["name"], item["bytes"]))

    for name, p_bytes in pdf_files:
        try:
            doc = fitz.open(stream=p_bytes, filetype="pdf")
            for page in doc:
                for img in page.get_images(full=True):
                    try:
                        base_image = doc.extract_image(img[0])
                        if base_image:
                            img_bytes = base_image["image"]
                            h = get_image_hash(img_bytes)
                            if h not in image_bytes_map:
                                image_bytes_map[h] = img_bytes
                                image_freq[h] = 1
                            else:
                                image_freq[h] += 1
                    except Exception:
                        continue
            doc.close()
        except Exception:
            continue
            
    # Sort hashes by frequency (descending order)
    sorted_hashes = sorted(image_freq.keys(), key=lambda k: image_freq[k], reverse=True)
    return sorted_hashes, image_freq, image_bytes_map

def process_pdf(file_bytes, hashes_to_remove):
    """Replaces selected images at the object level with an invisible pixel."""
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    modifications_made = False

    if doc.page_count > 0:
        xrefs_to_blank = set()
        
        # 1. Identify the internal xref IDs of the images we want to remove
        for page in doc:
            for img in page.get_images(full=True):
                xref = img[0]
                if xref in xrefs_to_blank:
                    continue
                try:
                    base_image = doc.extract_image(xref)
                    if base_image:
                        img_hash = get_image_hash(base_image["image"])
                        if img_hash in hashes_to_remove:
                            xrefs_to_blank.add(xref)
                except Exception:
                    continue
        
        # 2. Neutralize the images at the source
        if xrefs_to_blank:
            for xref in xrefs_to_blank:
                # Convert the image dictionary to a 1x1 invisible ImageMask
                doc.xref_set_key(xref, "ImageMask", "true")
                doc.xref_set_key(xref, "Filter", "null")
                doc.xref_set_key(xref, "Width", "1")
                doc.xref_set_key(xref, "Height", "1")
                doc.xref_set_key(xref, "BitsPerComponent", "1")
                doc.xref_set_key(xref, "ColorSpace", "null")
                doc.xref_set_key(xref, "SMask", "null")
                doc.xref_set_key(xref, "Mask", "null")
                doc.xref_set_key(xref, "Decode", "null")
                
                # Overwrite the actual stream with a transparent byte
                doc.update_stream(xref, b"\x00")
                modifications_made = True

    if modifications_made:
        out_buffer = io.BytesIO()
        # garbage=4 will optimize the PDF and remove the old image data entirely
        doc.save(out_buffer, garbage=4, deflate=True)
        doc.close()
        return out_buffer.getvalue()
    else:
        doc.close()
        return file_bytes
# --- Main App Execution ---
uploaded_files = st.file_uploader(
    "Choose PDF or ZIP files", type=["pdf", "zip"], accept_multiple_files=True
)

if uploaded_files:
    # Read files into memory to avoid Streamlit buffer seek issues during caching
    file_data = [{"name": f.name, "bytes": f.getvalue()} for f in uploaded_files]
    
    with st.spinner("Scanning documents for images..."):
        sorted_hashes, image_freq, image_bytes_map = scan_for_images(file_data)
    
    if not sorted_hashes:
        st.info("No images found in the uploaded documents.")
    else:
        st.write("### 🔍 Detected Images")
        st.write("Select the images you want to permanently strip from the files. They are sorted by how frequently they appear.")
        
        selected_hashes = set()
        cols = st.columns(3)
        
        for idx, h in enumerate(sorted_hashes):
            with cols[idx % 3]:
                try:
                    # using width='stretch' as use_container_width is deprecating
                    st.image(image_bytes_map[h], width="stretch")
                except Exception:
                    st.warning("⚠️ Preview unavailable (corrupted data)")
                    
                st.caption(f"Occurrences: {image_freq[h]}")
                if st.checkbox("Remove", key=f"chk_{h}"):
                    selected_hashes.add(h)
        
        if selected_hashes:
            st.write("---")
            if st.button("⚙️ Process Files & Remove Selected Images"):
                st.write("### Output Files")
                
                for item in file_data:
                    if item["name"].lower().endswith('.zip'):
                        with st.spinner(f"Processing ZIP: {item['name']}..."):
                            output_zip = io.BytesIO()
                            with zipfile.ZipFile(output_zip, 'w', zipfile.ZIP_DEFLATED) as out_zip:
                                with zipfile.ZipFile(io.BytesIO(item["bytes"])) as in_zip:
                                    for z_item in in_zip.infolist():
                                        if z_item.is_dir():
                                            out_zip.writestr(z_item, b'')
                                            continue
                                        z_data = in_zip.read(z_item.filename)
                                        if z_item.filename.lower().endswith('.pdf'):
                                            try:
                                                processed = process_pdf(z_data, selected_hashes)
                                                out_zip.writestr(z_item, processed)
                                            except Exception:
                                                out_zip.writestr(z_item, z_data)
                                        else:
                                            out_zip.writestr(z_item, z_data)
                            
                            st.download_button(
                                label=f"📥 Download Cleaned {item['name']}",
                                data=output_zip.getvalue(),
                                file_name=f"cleaned_{item['name']}",
                                mime="application/zip",
                                key=f"dl_{item['name']}"
                            )
                    
                    elif item["name"].lower().endswith('.pdf'):
                        with st.spinner(f"Processing PDF: {item['name']}..."):
                            try:
                                processed = process_pdf(item["bytes"], selected_hashes)
                                st.download_button(
                                    label=f"📥 Download Cleaned {item['name']}",
                                    data=processed,
                                    file_name=f"cleaned_{item['name']}",
                                    mime="application/pdf",
                                    key=f"dl_{item['name']}"
                                )
                            except Exception as e:
                                st.error(f"Failed processing {item['name']}: {e}")
