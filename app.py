import os
import cv2
import time
import threading
from flask import Flask, render_template, jsonify, Response
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
import monitor

# Carrega as variáveis do .env local
load_dotenv()

app = Flask(__name__)

# Configura e inicia o Scheduler para rodar em segundo plano
scheduler = BackgroundScheduler()
check_interval = int(os.environ.get("CHECK_INTERVAL_MINUTES", 5))

# Adiciona a tarefa principal do arquivo monitor.py
scheduler.add_job(func=monitor.check_print_status, trigger="interval", minutes=check_interval)
scheduler.start()

import queue

# ------ LÓGICA DE PROCESSAMENTO LOCAL DE VÍDEO (OPENCV COM BUFFER) ------
class VideoCamera(object):
    def __init__(self):
        self.camera_url = os.environ.get("IP_WEBCAM_URL", "").rstrip("/")
        self.video_url = f"{self.camera_url}/video" if self.camera_url else None
        self.cap = None
        
        # Fila para armazenar até 500 frames (aprox 20 segundos de vídeo a 24fps)
        self.frame_buffer = queue.Queue(maxsize=500)
        self.running = True
        
        if self.video_url:
            self.thread = threading.Thread(target=self.update, args=())
            self.thread.daemon = True
            self.thread.start()
            
    def update(self):
        """Thread que puxa frames do Tailscale o mais rápido possível e guarda na fila"""
        while self.running:
            if not self.cap or not self.cap.isOpened():
                try:
                    self.cap = cv2.VideoCapture(self.video_url)
                except Exception as e:
                    print(f"Erro ao abrir câmera OpenCV: {e}")
                time.sleep(1)
                continue
                
            success, frame = self.cap.read()
            if success:
                # Comprime o frame imediatamente para não consumir muita memória RAM
                ret, buffer = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
                frame_bytes = buffer.tobytes()
                
                # Se a fila encheu, joga o mais velho fora para colocar o mais novo
                if self.frame_buffer.full():
                    try:
                        self.frame_buffer.get_nowait()
                    except queue.Empty:
                        pass
                
                self.frame_buffer.put(frame_bytes)
            else:
                self.cap.release()
                self.cap = None
                time.sleep(0.5)

    def get_frame(self):
        """Puxa um frame do buffer se houver"""
        try:
            return self.frame_buffer.get_nowait()
        except queue.Empty:
            return None

# Instância global da câmera
camera = None

@app.route('/video_feed')
def video_feed():
    """Rota que entrega o vídeo processado pelo OpenCV com atraso proposital."""
    global camera
    if not camera:
        camera = VideoCamera()
        
    def gen():
        target_fps = 24
        frame_time = 1.0 / target_fps
        
        # O SISTEMA DE BUFFERING (O DELAY DE 10 SEGUNDOS)
        # 10 segundos * 24 fps = 240 frames teóricos. 
        # Vamos esperar carregar pelo menos 60 frames antes de soltar o vídeo para garantir gordura de fluidez.
        while camera.frame_buffer.qsize() < 60:
            time.sleep(0.5)
            
        yield b'--frame\r\n'
        
        while True:
            start_time = time.time()
            frame_bytes = camera.get_frame()
            
            if frame_bytes:
                yield (b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n--frame\r\n')
            else:
                # Se o buffer esvaziou (a internet engasgou muito), pausa para encher um pouco
                time.sleep(0.5)
            
            # Controle rigoroso de FPS na entrega para o navegador
            elapsed = time.time() - start_time
            wait_time = frame_time - elapsed
            if wait_time > 0:
                time.sleep(wait_time)
            else:
                time.sleep(0.005) # Previne uso de CPU a 100%

    return Response(gen(), mimetype='multipart/x-mixed-replace; boundary=frame')
# -----------------------------------------------------------

@app.route('/')
def index():
    """Renderiza o painel principal."""
    # Passamos True para avisar o HTML que a rota existe
    camera_configured = bool(os.environ.get("IP_WEBCAM_URL"))
    return render_template('index.html', camera_configured=camera_configured)

@app.route('/status')
def status():
    """API para o frontend buscar o status da IA via Javascript."""
    return jsonify({
        "last_check_time": monitor.last_check_time.strftime("%d/%m/%Y %H:%M:%S") if monitor.last_check_time else "Nenhuma",
        "last_check_status": monitor.last_check_status,
        "is_failing": monitor.is_failing
    })

@app.route('/force_check', methods=['POST'])
def force_check():
    """Rota para forçar a checagem manual da IA."""
    try:
        monitor.check_print_status()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

if __name__ == '__main__':
    # Roda a primeira checagem logo que o servidor iniciar
    try:
        scheduler.add_job(func=monitor.check_print_status, trigger="date")
    except Exception as e:
        print(f"Não foi possível iniciar a checagem imediata: {e}")
        
    print("Iniciando painel web em http://localhost:5050")
    app.run(host='0.0.0.0', port=5050, debug=False, threaded=True)
