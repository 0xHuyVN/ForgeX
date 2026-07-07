import sys
import json
import os as _os


def _load_nllb(model_name=None):
    from transformers import AutoTokenizer, AutoModelForSeq2SeqLM
    import torch
    _local_path = _os.path.expanduser("~/.cache/nllb_manual")
    allowed = {
        "facebook/nllb-200-distilled-600M",
        "facebook/nllb-200-distilled-1.3B",
    }
    if _os.path.isdir(_local_path) and _os.path.exists(_os.path.join(_local_path, "pytorch_model.bin")):
        model_name = _local_path
    elif model_name not in allowed:
        model_name = "facebook/nllb-200-distilled-1.3B"
    device = "cuda" if torch.cuda.is_available() else "cpu"
    tokenizer = AutoTokenizer.from_pretrained(model_name)
    model = AutoModelForSeq2SeqLM.from_pretrained(model_name, low_cpu_mem_usage=True).to(device)
    return tokenizer, model, device


def _load_marian():
    from transformers import AutoTokenizer, AutoModelForSeq2SeqLM
    import torch
    device = "cuda" if torch.cuda.is_available() else "cpu"
    tokenizer = AutoTokenizer.from_pretrained("Helsinki-NLP/opus-mt-zh-vi")
    model = AutoModelForSeq2SeqLM.from_pretrained("Helsinki-NLP/opus-mt-zh-vi").to(device)
    return tokenizer, model, device


def _load_m2m100(model_name=None):
    from transformers import M2M100ForConditionalGeneration, M2M100Tokenizer
    import torch
    allowed = {
        "facebook/m2m100_418M",
        "facebook/m2m100_1.2B",
    }
    if model_name not in allowed:
        model_name = "facebook/m2m100_418M"
    device = "cuda" if torch.cuda.is_available() else "cpu"
    tokenizer = M2M100Tokenizer.from_pretrained(model_name)
    model = M2M100ForConditionalGeneration.from_pretrained(model_name).to(device)
    return tokenizer, model, device


def _load_seamless(model_name=None):
    from transformers import AutoProcessor
    import torch
    try:
        from transformers import SeamlessM4TForTextToText as SeamlessModel
    except ImportError:
        from transformers import SeamlessM4TModel as SeamlessModel
    allowed = {
        "facebook/hf-seamless-m4t-medium",
        "facebook/seamless-m4t-v2-large",
    }
    if model_name not in allowed:
        model_name = "facebook/hf-seamless-m4t-medium"
    device = "cuda" if torch.cuda.is_available() else "cpu"
    processor = AutoProcessor.from_pretrained(model_name)
    model = SeamlessModel.from_pretrained(model_name).to(device)
    return processor, model, device


def translate_nllb(text, src, tgt, tokenizer, model, device):
    lang_map = {
        "vi": "vie_Latn", "en": "eng_Latn", "zh": "zho_Hans",
        "ja": "jpn_Jpan", "ko": "kor_Hang", "th": "tha_Thai",
        "fr": "fra_Latn", "de": "deu_Latn", "es": "spa_Latn",
        "ru": "rus_Cyrl", "ar": "ara_Arab", "pt": "por_Latn",
        "id": "ind_Latn", "ms": "zsm_Latn", "tl": "tgl_Latn",
        "lo": "lao_Laoo", "km": "khm_Khmr", "my": "mya_Mymr",
    }
    src_code = lang_map.get(src, src)
    tgt_code = lang_map.get(tgt, tgt)
    import torch
    tokenizer.src_lang = src_code
    tgt_token_id = tokenizer.convert_tokens_to_ids(tgt_code)
    lines = text.split("\n")
    # Translate only non-empty lines; preserve empty ones for alignment
    non_empty_indices = [i for i, l in enumerate(lines) if l.strip()]
    non_empty_texts = [lines[i].strip() for i in non_empty_indices]
    BATCH = 32
    translated_map = {}
    for i in range(0, len(non_empty_texts), BATCH):
        batch = non_empty_texts[i:i+BATCH]
        inputs = tokenizer(batch, return_tensors="pt", padding=True, truncation=True, max_length=512).to(device)
        with torch.no_grad():
            outputs = model.generate(
                **inputs,
                forced_bos_token_id=tgt_token_id,
                max_length=160,
                num_beams=4,
                repetition_penalty=1.35,
                no_repeat_ngram_size=3,
            )
        decoded = tokenizer.batch_decode(outputs, skip_special_tokens=True)
        for j, d in enumerate(decoded):
            translated_map[non_empty_indices[i + j]] = d.strip()
    # Reconstruct preserving empty lines
    result = []
    for i in range(len(lines)):
        if i in translated_map:
            result.append(translated_map[i])
        else:
            result.append("")
    return "\n".join(result)


def translate_marian(text, src, tgt, tokenizer, model, device):
    import torch
    inputs = tokenizer(text, return_tensors="pt", truncation=True, max_length=512).to(device)
    outputs = model.generate(**inputs, max_length=512)
    return tokenizer.decode(outputs[0], skip_special_tokens=True)


def translate_m2m100(text, src, tgt, tokenizer, model, device):
    lang_map = {
        "zh": "zh", "en": "en", "vi": "vi", "ja": "ja", "ko": "ko",
        "fr": "fr", "de": "de", "es": "es", "ru": "ru", "ar": "ar",
        "pt": "pt", "it": "it", "th": "th", "id": "id",
    }
    src_code = lang_map.get(src, src)
    tgt_code = lang_map.get(tgt, tgt)
    tokenizer.src_lang = src_code
    lines = text.split("\n")
    non_empty_indices = [i for i, line in enumerate(lines) if line.strip()]
    non_empty_texts = [lines[i].strip() for i in non_empty_indices]
    translated_map = {}
    import torch
    for i in range(0, len(non_empty_texts), 16):
        batch = non_empty_texts[i:i + 16]
        inputs = tokenizer(batch, return_tensors="pt", padding=True, truncation=True, max_length=512).to(device)
        with torch.no_grad():
            outputs = model.generate(
                **inputs,
                forced_bos_token_id=tokenizer.get_lang_id(tgt_code),
                max_length=160,
                num_beams=4,
            )
        decoded = tokenizer.batch_decode(outputs, skip_special_tokens=True)
        for j, value in enumerate(decoded):
            translated_map[non_empty_indices[i + j]] = value.strip()
    return "\n".join(translated_map.get(i, "") for i in range(len(lines)))


def translate_seamless(text, src, tgt, processor, model, device):
    lang_map = {
        "en": "eng", "vi": "vie", "zh": "cmn", "ja": "jpn", "ko": "kor",
        "fr": "fra", "de": "deu", "es": "spa", "ru": "rus", "ar": "arb",
        "pt": "por", "it": "ita", "th": "tha", "id": "ind",
    }
    src_code = lang_map.get(src, src)
    tgt_code = lang_map.get(tgt, tgt)
    lines = text.split("\n")
    out_lines = []
    import torch
    for line in lines:
        if not line.strip():
            out_lines.append("")
            continue
        inputs = processor(text=line.strip(), src_lang=src_code, return_tensors="pt").to(device)
        with torch.no_grad():
            try:
                output_tokens = model.generate(**inputs, tgt_lang=tgt_code, generate_speech=False)
            except TypeError:
                output_tokens = model.generate(**inputs, tgt_lang=tgt_code)
        if isinstance(output_tokens, tuple):
            output_tokens = output_tokens[0]
        try:
            decoded = processor.batch_decode(output_tokens, skip_special_tokens=True)
            out_lines.append(decoded[0].strip() if decoded else "")
        except Exception:
            token_ids = output_tokens[0].tolist() if hasattr(output_tokens[0], "tolist") else output_tokens[0]
            out_lines.append(processor.decode(token_ids, skip_special_tokens=True).strip())
    return "\n".join(out_lines)


def main():
    # Read first request to decide which engine and load model
    first = json.loads(sys.stdin.readline())
    engine = first["engine"]

    model_name = first.get("model")

    if engine == "nllb":
        tokenizer, model, device = _load_nllb(model_name)
        translate = lambda text, src, tgt: translate_nllb(text, src, tgt, tokenizer, model, device)
    elif engine == "marian":
        tokenizer, model, device = _load_marian()
        translate = lambda text, src, tgt: translate_marian(text, src, tgt, tokenizer, model, device)
    elif engine == "m2m100":
        tokenizer, model, device = _load_m2m100(model_name)
        translate = lambda text, src, tgt: translate_m2m100(text, src, tgt, tokenizer, model, device)
    elif engine == "seamless":
        processor, model, device = _load_seamless(model_name)
        translate = lambda text, src, tgt: translate_seamless(text, src, tgt, processor, model, device)
    else:
        sys.stderr.write(f"Unknown engine: {engine}\n")
        sys.exit(1)

    # Process first request
    try:
        result = translate(first["text"], first["src"], first["tgt"])
        sys.stdout.write(json.dumps({"result": result, "error": None}) + "\n")
        sys.stdout.flush()
    except Exception as e:
        sys.stdout.write(json.dumps({"result": None, "error": str(e)}) + "\n")
        sys.stdout.flush()

    # Process subsequent requests (model stays loaded)
    for line in sys.stdin:
        try:
            req = json.loads(line)
            result = translate(req["text"], req["src"], req["tgt"])
            sys.stdout.write(json.dumps({"result": result, "error": None}) + "\n")
        except Exception as e:
            sys.stdout.write(json.dumps({"result": None, "error": str(e)}) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
