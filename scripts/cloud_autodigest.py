import os
import subprocess
import glob
import json
import time
import random
import datetime
import google.generativeai as genai

CWD = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP_DIR = os.path.join(CWD, "tmp_scrape")
OUT_FILE = os.path.join(CWD, "weekly_digest_export.md")
CHANNELS_FILE = os.path.join(CWD, "canais.txt")

def run_yt_dlp_with_backoff(cmd, max_retries=4):
    retries = 0
    while retries <= max_retries:
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8')
            if "HTTP Error 429" in result.stderr:
                sleep_time = (2 ** retries) + random.uniform(1, 4)
                print(f"   [RATE LIMIT 429] Detetado. Agendando nova tentativa {retries+1}/{max_retries} em {sleep_time:.2f}s...")
                time.sleep(sleep_time)
                retries += 1
                continue
            return result
        except Exception as e:
            print(f"Erro subprocesso: {e}")
            break
    print("   [FALHA] Não foi possível contornar os rate limits aps tentativas.")
    return None

def main():
    print("🚀 Iniciando Cloud Auto-Digest Engine...")
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("ERRO CRÍTICO: GEMINI_API_KEY não foi encontrada nas variáveis de ambiente. Terminar o Job.")
        return

    if not os.path.exists(TMP_DIR):
        os.makedirs(TMP_DIR, exist_ok=True)
    else:
        for f in glob.glob(os.path.join(TMP_DIR, "*")): os.remove(f)

    with open(CHANNELS_FILE, "r", encoding="utf-8") as f:
        canais = [line.strip() for line in f.readlines() if line.strip()]

    raw_markdown = ""
    videos_ignorados = []

    for canal in canais:
        print(f"\n=> 🕵️‍♂️ Analisando Canal: {canal}")
        cmd_info = [
            "python", "-m", "yt_dlp",
            "--dateafter", "today-1day",
            "--playlist-end", "5",
            "--dump-json",
            "--impersonate", "chrome",
            canal
        ]
        
        result = run_yt_dlp_with_backoff(cmd_info)
        if result and result.stdout:
            for line in result.stdout.strip().split('\n'):
                if not line.strip(): continue
                try:
                    info = json.loads(line)
                    duration = info.get('duration', 0)
                    title = info.get('title', 'Unknown')
                    channel = info.get('channel', canal)
                    video_url = info.get('webpage_url', '')

                    if duration > 2700:
                        print(f"   [Ignorado >45m] {title}")
                        videos_ignorados.append(f"{channel} === {title}")
                    else:
                        print(f"   [Descarregando Legendas] {title}")
                        cmd_sub = [
                            "python", "-m", "yt_dlp",
                            "--write-auto-sub", "--write-sub", "--sub-langs", "en,pt", 
                            "--skip-download", 
                            "--impersonate", "chrome",
                            "-o", os.path.join(TMP_DIR, "%(channel)s === %(title)s.%(ext)s"),
                            video_url
                        ]
                        run_yt_dlp_with_backoff(cmd_sub)
                except Exception:
                    pass

    # Aggregating Subtitles
    vtt_files = glob.glob(os.path.join(TMP_DIR, "*.vtt"))
    if not vtt_files and not videos_ignorados:
        print("Nenhum video novo processado hoje.")
        return

    for vtt_file in vtt_files:
        video_name = os.path.basename(vtt_file).replace(".vtt", "")
        raw_markdown += f"\n\n## 📼 {video_name}\n\n"
        try:
            with open(vtt_file, "r", encoding="utf-8") as v:
                raw_markdown += v.read()
        except:
            pass

    if videos_ignorados:
        raw_markdown += "\n\n## ⚠️ Videos Longos Ignorados (>45 min)\n"
        for v in videos_ignorados:
            raw_markdown += f"- {v}\n"

    print("\n🧠 Enviando bruto para Gemini 1.5 Flash API...")
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel('gemini-1.5-flash')
    
    prompt = f"""
Atuas como um assistente intelectual de curadoria.
Recebes abaixo as transcrições brutas dos meus canais de YouTube das últimas 24H.
A tua tarefa é limpar o ruído (como patrocínios ou intros logas), sumariar a Tese Principal e as lições vitais de cada vídeo gerando um Super-Digest altamente denso e formatado em Markdown pronto a ser importado para o Obsidian.

Adiciona Frontmatter apropriado.
Usa as tags: [youtube, digest_matinal, multi_canal, diario] e a data de hoje {datetime.datetime.now().strftime('%Y-%m-%d')}
Usa o Título: "# 🌅 Super-Digest Matinal: YouTube (24H)"

Organiza por blocos elegantes e usa Emojis apropriados. Cita o nome do canal em cada bloco.

== TEXTO BRUTO ==:
{raw_markdown}
"""
    
    try:
        response = model.generate_content(prompt)
        with open(OUT_FILE, "w", encoding="utf-8") as f:
            f.write(response.text)
        print(f"✅ Sucesso! O LLM gerou o relatorio: {OUT_FILE}")
    except Exception as e:
        print(f"❌ Erro grave na geração IA: {e}")

if __name__ == "__main__":
    main()
