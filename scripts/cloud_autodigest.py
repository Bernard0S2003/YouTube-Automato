import os
import re
import datetime
import feedparser
import requests
from youtube_transcript_api import YouTubeTranscriptApi
import google.generativeai as genai

CWD = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_FILE = os.path.join(CWD, "weekly_digest_export.md")
CHANNELS_FILE = os.path.join(CWD, "canais.txt")
MAX_VIDEO_DURATION_SECS = 2700  # 45 minutos


def get_channel_id(channel_url):
    """Extrai o channel_id real a partir de um URL de canal (@handle)."""
    try:
        resp = requests.get(channel_url, timeout=15)
        match = re.search(r'"externalId"\s*:\s*"(UC[^"]+)"', resp.text)
        if match:
            return match.group(1)
        match = re.search(r'channel_id=([^"&]+)', resp.text)
        if match:
            return match.group(1)
    except Exception as e:
        print(f"   [ERRO] Falha ao resolver channel_id de {channel_url}: {e}")
    return None


def get_recent_videos(channel_id, max_age_hours=48):
    """Usa o RSS Feed publico do YouTube para listar os videos recentes."""
    feed_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    feed = feedparser.parse(feed_url)
    
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=max_age_hours)
    recent = []
    
    for entry in feed.entries:
        published = datetime.datetime(*entry.published_parsed[:6], tzinfo=datetime.timezone.utc)
        if published >= cutoff:
            video_id = entry.yt_videoid
            recent.append({
                "id": video_id,
                "title": entry.title,
                "author": entry.author,
                "url": entry.link,
                "published": published.isoformat()
            })
    return recent


def get_transcript(video_id):
    """Extrai a transcript de um video via youtube-transcript-api (sem yt-dlp)."""
    try:
        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
        
        # Tentar primeiro legendas manuais em EN ou PT
        for lang in ['en', 'pt']:
            try:
                transcript = transcript_list.find_transcript([lang])
                segments = transcript.fetch()
                return " ".join([s.text for s in segments])
            except Exception:
                pass
        
        # Fallback: legendas auto-geradas
        try:
            transcript = transcript_list.find_generated_transcript(['en', 'pt'])
            segments = transcript.fetch()
            return " ".join([s.text for s in segments])
        except Exception:
            pass
            
    except Exception as e:
        print(f"   [SEM LEGENDAS] {video_id}: {e}")
    return None


def main():
    print("Iniciando Cloud Auto-Digest Engine v2 (RSS + Transcript API)...")

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("ERRO CRITICO: GEMINI_API_KEY nao encontrada. A terminar.")
        return

    with open(CHANNELS_FILE, "r", encoding="utf-8") as f:
        canais = [line.strip() for line in f.readlines() if line.strip()]

    all_transcripts = []
    videos_sem_legendas = []

    for canal_url in canais:
        print(f"\n=> Analisando Canal: {canal_url}")
        
        channel_id = get_channel_id(canal_url)
        if not channel_id:
            print(f"   [SKIP] Nao foi possivel resolver o channel_id.")
            continue
        
        print(f"   Channel ID: {channel_id}")
        recent_videos = get_recent_videos(channel_id, max_age_hours=48)
        
        if not recent_videos:
            print(f"   Sem videos recentes.")
            continue
        
        for video in recent_videos:
            print(f"   [VIDEO] {video['title']}")
            
            transcript_text = get_transcript(video["id"])
            
            if transcript_text:
                all_transcripts.append({
                    "channel": video["author"],
                    "title": video["title"],
                    "url": video["url"],
                    "transcript": transcript_text
                })
                print(f"   [OK] Transcript extraida ({len(transcript_text)} chars)")
            else:
                videos_sem_legendas.append(f"{video['author']} - {video['title']}")

    # Se nao ha nada para processar
    today = datetime.datetime.now().strftime('%Y-%m-%d')
    if not all_transcripts:
        print("Nenhuma transcript recolhida hoje.")
        with open(OUT_FILE, "w", encoding="utf-8") as f:
            f.write(f"---\ntags: [youtube, digest_matinal, multi_canal, diario]\ndate: {today}\n---\n")
            f.write("# Super-Digest Matinal: YouTube (24H)\n\n")
            f.write("Nenhum dos canais inspecionados publicou videos com legendas disponiveis nas ultimas 48 horas.\n")
        return

    # Construir contexto bruto para o LLM
    raw_context = ""
    for t in all_transcripts:
        raw_context += f"\n\n## Canal: {t['channel']} | Video: {t['title']}\nURL: {t['url']}\n\nTranscript:\n{t['transcript']}\n\n---\n"

    if videos_sem_legendas:
        raw_context += "\n\n## Videos Sem Legendas Disponiveis\n"
        for v in videos_sem_legendas:
            raw_context += f"- {v}\n"

    print(f"\nEnviando {len(all_transcripts)} transcripts para Gemini Flash...")
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel('gemini-1.5-flash')

    prompt = f"""Atuas como um assistente intelectual de curadoria.
Recebes abaixo as transcricoes dos meus canais de YouTube das ultimas 24-48H.
A tua tarefa e limpar o ruido (patrocinios, intros, outros), sumariar a Tese Principal e as licoes vitais de cada video, gerando um Super-Digest altamente denso e formatado em Markdown pronto a ser importado para o Obsidian.

Regras obrigatorias:
- Adiciona Frontmatter YAML no topo com tags: [youtube, digest_matinal, multi_canal, diario] e date: {today}
- Titulo principal: "# Super-Digest Matinal: YouTube (24H)"
- Organiza cada video como um bloco com o nome do canal, titulo do video e um link clicavel para o video original
- Para cada video escreve: a Tese principal (1-2 frases), os Pontos-Chave (bullet points), e um Veredito Final (1 frase)
- Usa emojis apropriados nos cabecalhos
- Escreve em Portugues de Portugal

== TRANSCRICOES BRUTAS ==:
{raw_context}
"""

    try:
        response = model.generate_content(prompt)
        with open(OUT_FILE, "w", encoding="utf-8") as f:
            f.write(response.text)
        print(f"Sucesso! Relatorio gerado: {OUT_FILE}")
    except Exception as e:
        import sys
        print(f"Erro grave na geracao IA: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
