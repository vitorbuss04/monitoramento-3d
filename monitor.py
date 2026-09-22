import os
import smtplib
import requests
from email.message import EmailMessage
from google import genai
from google.genai import types
from datetime import datetime

# Variáveis globais para o painel ler o status atual
last_check_time = None
last_check_status = "Nenhuma verificação realizada ainda."
is_failing = False

def get_camera_snapshot():
    """Baixa o frame atual da câmera IP Webcam."""
    base_url = os.environ.get("IP_WEBCAM_URL", "").rstrip("/")
    if not base_url:
        return None
    url = f"{base_url}/shot.jpg"
    try:
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            return response.content
    except Exception as e:
        print(f"Erro ao capturar imagem da câmera: {e}")
    return None

def analyze_image_with_gemini(image_bytes):
    """Envia a imagem para a API do Gemini para verificar falhas."""
    try:
        # Configura a nova API
        client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
        
        prompt = (
            "Você é um assistente de monitoramento de impressão 3D. "
            "Examine esta imagem da câmera da impressora 3D. "
            "A impressão falhou? Procure por 'espaguete' (fios soltos de filamento bagunçados), "
            "descolamento da base, ou a peça fora do lugar. "
            "Responda EXATAMENTE no seguinte formato em Português:\n"
            "FALHA: SIM ou NAO\n"
            "MOTIVO: (uma breve explicação de 1 ou 2 frases do que você vê)"
        )
        
        import time
        
        # Tenta até 3 vezes em caso de servidor ocupado (Erro 503)
        for attempt in range(3):
            try:
                response = client.models.generate_content(
                    model='gemini-3.5-flash-lite',
                    contents=[
                        types.Part.from_bytes(data=image_bytes, mime_type='image/jpeg'),
                        prompt,
                    ]
                )
                break # Sai do loop se der sucesso
            except Exception as e:
                if '503' in str(e) and attempt < 2:
                    print(f"Servidor da IA ocupado. Aguardando 3s para tentar novamente... (Tentativa {attempt+1}/3)")
                    time.sleep(3)
                else:
                    raise e
        
        text = response.text.upper()
        
        is_fail = "FALHA: SIM" in text
        
        # Extrai o motivo ignorando quebras de linha
        reason_raw = response.text.split("MOTIVO:")[-1].strip() if "MOTIVO:" in response.text else "Análise concluída."
        reason = reason_raw.replace('\n', ' ')
        
        return is_fail, reason
    except Exception as e:
        print(f"Erro na análise do Gemini: {e}")
        return False, f"Erro na comunicação com a IA: {e}"

def send_email_alert(image_bytes, reason):
    """Envia um e-mail de alerta com a imagem em anexo."""
    try:
        msg = EmailMessage()
        msg['Subject'] = '⚠️ ALERTA: Possível falha na impressão 3D!'
        msg['From'] = os.environ.get("SMTP_USER")
        msg['To'] = os.environ.get("ALERT_EMAIL")
        
        msg.set_content(
            "A Inteligência Artificial detectou uma possível falha na sua impressão 3D.\n\n"
            f"Motivo apontado pela IA:\n{reason}\n\n"
            "Verifique a imagem em anexo e abra o seu aplicativo da Bambu Lab para confirmar e pausar se necessário."
        )
        
        if image_bytes:
            msg.add_attachment(image_bytes, maintype='image', subtype='jpeg', filename='snapshot.jpg')
            
        with smtplib.SMTP(os.environ.get("SMTP_SERVER"), int(os.environ.get("SMTP_PORT", 587))) as server:
            server.starttls()
            server.login(os.environ.get("SMTP_USER"), os.environ.get("SMTP_PASSWORD"))
            server.send_message(msg)
        print("E-mail de alerta enviado com sucesso!")
    except Exception as e:
        print(f"Erro ao enviar e-mail: {e}")

def check_print_status():
    """Função principal agendada que coordena a verificação."""
    global last_check_time, last_check_status, is_failing
    
    print(f"[{datetime.now()}] Iniciando verificação da impressão...")
    image_bytes = get_camera_snapshot()
    
    if not image_bytes:
        last_check_time = datetime.now()
        last_check_status = "Erro: Não foi possível capturar imagem da câmera. Verifique a URL do IP Webcam."
        return

    is_fail, reason = analyze_image_with_gemini(image_bytes)
    
    last_check_time = datetime.now()
    last_check_status = reason
    is_failing = is_fail
    
    if is_fail:
        print(f"[{datetime.now()}] FALHA DETECTADA: {reason}")
        send_email_alert(image_bytes, reason)
    else:
        print(f"[{datetime.now()}] Impressão parece OK.")
