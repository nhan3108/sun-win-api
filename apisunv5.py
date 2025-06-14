from flask import Flask, request, jsonify
import logging
from collections import Counter
import threading
import websocket
import json
import ssl
import time
import backoff
import os

app = Flask(__name__)

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Global variables
id_phien = 0
ket_qua = []
game_data = {}
game_data_lock = threading.Lock()
ws_connected = False
so_lan_dung = 0
so_lan_sai = 0

# WebSocket initialization messages
messages_to_send = [
    [1, "MiniGame", "SC_tapchoitx1dayyy", "tapchoitx1dayyy", {
        "info": "{\"ipAddress\":\"14.243.82.39\",\"userId\":\"9a88bfc9-af53-4bf0-83ff-660536c5d8fe\",\"username\":\"SC_tapchoitx1dayyy\",\"timestamp\":1749274560490,\"refreshToken\":\"31f1f2d79b0b4a0f91928b59ac89130a.4f0170c6cbf24a49acd6167103a18af2\"}",
        "signature": "0910B1DD4A8147D10889D1C6A1876461A465E7B0CDF34B291CCFB33762395E9A67B7037DDC15DDD73334566B1CE86E8996D4E65AEA67B9B03F18EFFC842A8A94F523671F8A8EDAF6B46D6304A994DCEE0E9731CEC3EC34099CD675C704402AA01662DFD58B42C59E7D8FB14BDB41855322358C905017BA5BCD4AA13DB6690785"
    }],
    [6, "MiniGame", "taixiuPlugin", {"cmd": 1005}]
]

# Initialize CSV log file
if not os.path.exists("du_lieu_phien.csv"):
    with open("du_lieu_phien.csv", "w", encoding="utf-8") as f:
        f.write("Phien,DuDoan,ThucTe,XucXac,Tong,DoTinCay,KetQua\n")

def tong_hop_du_doan():
    if len(ket_qua) < 5:
        t_count = ket_qua.count("t")
        x_count = ket_qua.count("x")
        if t_count == x_count:
            return "t", 50.0
        pred = "t" if t_count > x_count else "x"
        confidence = (max(t_count, x_count) / len(ket_qua)) * 100 if len(ket_qua) > 0 else 50.0
        return pred, confidence

    prediction_votes = {"t": 0, "x": 0}
    
    # Strategy 1: Simple Majority
    t_count = ket_qua.count("t")
    x_count = ket_qua.count("x")
    if t_count > x_count:
        prediction_votes["t"] += 2
    elif x_count > t_count:
        prediction_votes["x"] += 2
    
    # Strategy 2: Last N Pattern Matching
    for window_size in [2, 3, 4]:
        if len(ket_qua) >= window_size + 1:
            pattern_dict = {}
            for i in range(len(ket_qua) - window_size):
                key = tuple(ket_qua[i:i + window_size])
                next_val = ket_qua[i + window_size]
                if key not in pattern_dict:
                    pattern_dict[key] = {"t": 0, "x": 0}
                pattern_dict[key][next_val] += 1
            
            last_seq = tuple(ket_qua[-window_size:])
            if last_seq in pattern_dict:
                counts = pattern_dict[last_seq]
                if counts["t"] > counts["x"]:
                    prediction_votes["t"] += 1
                elif counts["x"] > counts["t"]:
                    prediction_votes["x"] += 1

    # Strategy 3: Alternating Pattern Detection
    alternating_t = all((i % 2 == 0 and ket_qua[i] == "t") or (i % 2 == 1 and ket_qua[i] == "x") 
                   for i in range(len(ket_qua)))
    alternating_x = all((i % 2 == 0 and ket_qua[i] == "x") or (i % 2 == 1 and ket_qua[i] == "t") 
                   for i in range(len(ket_qua)))
    
    if alternating_t and len(ket_qua) > 2:
        prediction_votes["x" if ket_qua[-1] == "t" else "t"] += 1
    elif alternating_x and len(ket_qua) > 2:
        prediction_votes["t" if ket_qua[-1] == "x" else "x"] += 1

    # Strategy 4: Consecutive Pattern Detection
    if len(ket_qua) > 0:
        last_val = ket_qua[-1]
        consecutive_count = 1
        for i in range(len(ket_qua)-2, -1, -1):
            if ket_qua[i] == last_val:
                consecutive_count += 1
            else:
                break
        
        if consecutive_count >= 3:
            prediction_votes["x" if last_val == "t" else "t"] += 1
        elif consecutive_count == 2:
            prediction_votes[last_val] += 1

    # Determine final prediction
    total_votes = sum(prediction_votes.values())
    if total_votes == 0:
        pred = "t" if t_count > x_count else "x"
        confidence = (max(t_count, x_count) / len(ket_qua)) * 100 if len(ket_qua) > 0 else 50.0
        return pred, confidence

    if prediction_votes["t"] > prediction_votes["x"]:
        final_prediction = "t"
        confidence = (prediction_votes["t"] / total_votes) * 100
    elif prediction_votes["x"] > prediction_votes["t"]:
        final_prediction = "x"
        confidence = (prediction_votes["x"] / total_votes) * 100
    else:
        final_prediction = "t" if t_count > x_count else "x"
        confidence = 50.0

    return final_prediction, min(confidence + (len(ket_qua) / 30) * 10, 95.0)

def on_message(ws, message):
    global id_phien, ket_qua, game_data, ws_connected, so_lan_dung, so_lan_sai
    try:
        data = json.loads(message)
        logger.debug(f"Received message: {data}")
        
        if isinstance(data, list) and len(data) >= 2:
            if isinstance(data[1], dict):
                cmd = data[1].get("cmd")
                if cmd == 1008 and "sid" in data[1]:
                    sid_new = data[1]["sid"]
                    if sid_new != id_phien:
                        id_phien = sid_new
                        logger.info(f"Updated session ID: {id_phien}")
                
                if cmd == 1003:
                    d1 = data[1].get("d1")
                    d2 = data[1].get("d2")
                    d3 = data[1].get("d3")
                    if all(isinstance(d, int) and 1 <= d <= 6 for d in [d1, d2, d3]):
                        total = d1 + d2 + d3
                        result_tx = "t" if total > 10 else "x"
                        with game_data_lock:
                            # Check previous prediction accuracy
                            if 'prediction_raw' in game_data:
                                prev_pred = game_data['prediction_raw']
                                if prev_pred == result_tx:
                                    so_lan_dung += 1
                                else:
                                    so_lan_sai += 1
                            
                            ket_qua.append(result_tx)
                            if len(ket_qua) > 20:
                                ket_qua.pop(0)
                            next_pred_raw, confidence = tong_hop_du_doan()
                            next_pred = "Tài" if next_pred_raw == "t" else "Xỉu"
                            game_data.update({
                                "current_session": id_phien,
                                "xuc_xac": [d1, d2, d3],
                                "tong": total,
                                "current_result": "Tài" if result_tx == "t" else "Xỉu",
                                "next_session": id_phien + 1,
                                "pattern": ''.join(ket_qua),
                                "prediction": next_pred,
                                "prediction_raw": next_pred_raw,
                                "confidence_percent": round(confidence, 2),
                                "so_lan_dung": so_lan_dung,
                                "so_lan_sai": so_lan_sai,
                                "ws_connected": True
                            })
                            with open("du_lieu_phien.csv", "a", encoding="utf-8") as f:
                                ket_qua_str = "Đúng" if next_pred_raw == result_tx else "Sai"
                                f.write(f"{id_phien},{next_pred},{'Tài' if result_tx == 't' else 'Xỉu'},{d1}-{d2}-{d3},{total},{round(confidence, 2)},{ket_qua_str}\n")
                    else:
                        logger.warning("Invalid dice values received")
            elif "ping" in str(data).lower():
                ws.send(json.dumps(["pong"]))
                
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse WebSocket message: {e}")
    except Exception as e:
        logger.error(f"Error in on_message: {e}")

def on_error(ws, error):
    global ws_connected
    ws_connected = False
    logger.error(f"WebSocket Error: {error}")
    if isinstance(error, websocket.WebSocketConnectionClosedException):
        logger.info("Connection closed, will attempt to reconnect...")

def on_close(ws, close_status_code, close_msg):
    global ws_connected
    ws_connected = False
    logger.warning(f"WebSocket Closed: code={close_status_code}, message={close_msg}")

def on_open(ws):
    global ws_connected
    ws_connected = True
    logger.info("WebSocket connection opened")
    
    # Send initialization messages
    try:
        for msg in messages_to_send:
            ws.send(json.dumps(msg))
            logger.info(f"Sent initialization message: {msg}")
            time.sleep(1)  # Wait briefly between messages
        
        # Start keep-alive thread
        threading.Thread(target=keep_alive, args=(ws,), daemon=True).start()
    except Exception as e:
        logger.error(f"Failed to send initialization messages: {e}")

def keep_alive(ws):
    while True:
        time.sleep(15)
        if not hasattr(ws, 'sock') or not ws.sock or not ws.sock.connected:
            logger.warning("WebSocket not connected. Stopping keep-alive.")
            break
        try:
            ws.send(json.dumps({"cmd": "ping"}))
            logger.debug("Sent ping")
        except Exception as e:
            logger.warning(f"Ping failed: {e}")
            break

@backoff.on_exception(backoff.expo, 
                     (websocket.WebSocketException, ConnectionError, Exception), 
                     max_tries=15, 
                     max_time=600,
                     jitter=backoff.full_jitter)
def run_websocket():
    headers = [
        "Host: websocket.azhkthg1.net",
        "Origin: https://web.sunwin.us",
        "User-Agent: Mozilla/5.0",
        "Connection: Upgrade",
        "Upgrade: websocket",
        "Sec-WebSocket-Version: 13"
    ]
    
    websocket_url = "wss://websocket.azhkthg1.net/websocket"
    logger.info(f"Connecting to WebSocket: {websocket_url}")
    
    ws = websocket.WebSocketApp(
        websocket_url,
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close,
        header=headers
    )
    
    ws.run_forever(
        sslopt={
            "cert_reqs": ssl.CERT_NONE,
            "ssl_version": ssl.PROTOCOL_TLS,
            "check_hostname": False
        },
        ping_interval=20,
        ping_timeout=10,
        reconnect=5
    )

def run_websocket_loop():
    while True:
        try:
            run_websocket()
        except Exception as e:
            logger.error(f"WebSocket crashed: {e}")
        logger.info("Reconnecting in 5 seconds...")
        time.sleep(5)

@app.route('/predict', methods=['POST'])
def predict_post():
    global id_phien, ket_qua, so_lan_dung, so_lan_sai
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No JSON data provided"}), 400
        sid = data.get('sid')
        if sid is None:
            return jsonify({"error": "Missing session ID (sid)"}), 400
        if sid != id_phien:
            id_phien = sid
        d1 = data.get('d1')
        d2 = data.get('d2')
        d3 = data.get('d3')
        if not all([d1, d2, d3]) or not all(isinstance(d, int) and 1 <= d <= 6 for d in [d1, d2, d3]):
            return jsonify({"error": "Invalid dice values"}), 400
        total = d1 + d2 + d3
        result_tx = "t" if total > 10 else "x"
        with game_data_lock:
            # Check previous prediction accuracy
            if 'prediction_raw' in game_data:
                prev_pred = game_data['prediction_raw']
                if prev_pred == result_tx:
                    so_lan_dung += 1
                else:
                    so_lan_sai += 1
            
            ket_qua.append(result_tx)
            if len(ket_qua) > 20:
                ket_qua.pop(0)
            next_pred_raw, confidence = tong_hop_du_doan()
            response = {
                "current_session": id_phien,
                "xuc_xac": [d1, d2, d3],
                "tong": total,
                "current_result": "Tài" if result_tx == "t" else "Xỉu",
                "next_session": id_phien + 1,
                "prediction": "Tài" if next_pred_raw == "t" else "Xỉu",
                "prediction_raw": next_pred_raw,
                "confidence_percent": round(confidence, 2),
                "pattern": ''.join(ket_qua),
                "so_lan_dung": so_lan_dung,
                "so_lan_sai": so_lan_sai,
                "ws_connected": ws_connected
            }
            game_data.update(response)
            return jsonify(response), 200
    except Exception as e:
        logger.error(f"POST /predict error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/predict', methods=['GET'])
def predict_get():
    with game_data_lock:
        if not game_data:
            next_pred_raw, confidence = tong_hop_du_doan()
            return jsonify({
                "current_session": id_phien,
                "xuc_xac": [],
                "tong": 0,
                "current_result": "No result",
                "next_session": id_phien + 1,
                "prediction": "Tài" if next_pred_raw == "t" else "Xỉu" if next_pred_raw else "No prediction",
                "confidence_percent": round(confidence, 2) if next_pred_raw else 0.0,
                "so_lan_dung": so_lan_dung,
                "so_lan_sai": so_lan_sai,
                "ws_connected": ws_connected
            }), 200
        game_data['ws_connected'] = ws_connected
        return jsonify(game_data), 200

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({
        "status": "ok", 
        "session_id": id_phien,
        "ws_connected": ws_connected,
        "so_lan_dung": so_lan_dung,
        "so_lan_sai": so_lan_sai
    }), 200

if __name__ == "__main__":
    logger.info("Starting Tài Xỉu API...")
    ws_thread = threading.Thread(target=run_websocket_loop, daemon=True)
    ws_thread.start()
    app.run(host='0.0.0.0', port=1234, debug=False, use_reloader=False)