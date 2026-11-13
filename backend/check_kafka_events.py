import sys
import json
import subprocess
import signal

TOPIC_SHORTCUTS = {
    "job": "fieldops.job.events",
    "jobs": "fieldops.job.events",
    "gps": "fieldops.gps.events",
    "notification": "fieldops.notification.events",
    "notifications": "fieldops.notification.events",
    "billing": "fieldops.billing.events",
    "payment": "fieldops.payment.events",
    "sla": "fieldops.sla.events",
    "audit": "fieldops.audit.events",
    "events": "fieldops.events",
}

ALL_TOPICS = [
    ("fieldops.job.events", "Job lifecycle (Created, Assigned, Accepted, In-Progress, Completed)"),
    ("fieldops.gps.events", "Technician live GPS coordinates and location pings"),
    ("fieldops.notification.events", "SMS, Push, and Email notifications sent to Techs/Customers"),
    ("fieldops.billing.events", "Job invoices, labor & material cost closures"),
    ("fieldops.payment.events", "Payment transactions and verification events"),
    ("fieldops.sla.events", "SLA monitoring, countdown warnings, and escalations"),
    ("fieldops.audit.events", "Enterprise security and operational audit streams"),
    ("fieldops.events", "Core system events stream"),
]

def show_topics():
    print(f"\n{'='*30} FIELDOPS KAFKA TOPICS {'='*30}")
    print(f"{'Topic Name':<32} | {'Shortcut':<12} | {'Description'}")
    print("-" * 90)
    for topic, desc in ALL_TOPICS:
        shortcut = [k for k, v in TOPIC_SHORTCUTS.items() if v == topic][0]
        print(f"{topic:<32} | {shortcut:<12} | {desc}")
    print("-" * 90)
    print()
    print("💡 If you want to see Specific topic JSON events 👇🏻 :")
    print()
    print("   python check_kafka_events.py job")
    print("   python check_kafka_events.py billing")
    print("   python check_kafka_events.py gps")
    print()
    print("💡If you want to see Real-time events in live use 👇🏻 :")
    print()
    print("   python check_kafka_events.py live job\n")

def print_formatted_event(idx, raw_line):
    raw_line = raw_line.strip()
    if not raw_line or not raw_line.startswith("{"):
        return
    try:
        data = json.loads(raw_line)
        topic = data.get("topic", "N/A")
        msg_id = data.get("message_id", "N/A")
        ts = data.get("timestamp", "N/A")
        corr_id = data.get("correlation_id", "N/A")
        
        payload = data.get("payload", {})
        event_type = payload.get("event_type") or data.get("message_type", "EVENT")
        job_id = payload.get("job_id", "N/A")
        tech_id = payload.get("technician_id", "N/A")
        
        print(f"\n{'='*25} EVENT #{idx} [{event_type.upper()}] {'='*25}")
        print(f"Topic:          {topic}")
        print(f"Message ID:     {msg_id}")
        print(f"Timestamp:      {ts}")
        print(f"Correlation ID: {corr_id}")
        if job_id != "N/A":
            print(f"Job ID:         {job_id}")
        if tech_id != "N/A":
            print(f"Technician ID:  {tech_id}")
        print("-" * 70)
        print("📦 JSON PAYLOAD:")
        print(json.dumps(data, indent=2))
        print("=" * 70)
    except Exception:
        print(raw_line)

def read_topic_history(topic_name, limit=5):
    print(f"\n🔍 Fetching last {limit} events from topic: '{topic_name}'...\n")
    cmd = [
        "docker", "exec", "fieldops-kafka",
        "/opt/kafka/bin/kafka-console-consumer.sh",
        "--bootstrap-server", "localhost:9092",
        "--topic", topic_name,
        "--from-beginning",
        "--max-messages", str(limit),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=8)
        lines = [l for l in proc.stdout.strip().split("\n") if l.strip().startswith("{")]
    except subprocess.TimeoutExpired as te:
        stdout = te.stdout.decode("utf-8", errors="ignore") if isinstance(te.stdout, bytes) else (te.stdout or "")
        lines = [l for l in stdout.strip().split("\n") if l.strip().startswith("{")]
    except Exception as e:
        print(f"❌ Error connecting to Kafka container: {e}")
        return

    if not lines:
        print(f"ℹ️  No messages found in topic '{topic_name}' yet.")
        print("   (Events will appear as actions are performed in the app).")
        return
    
    for idx, line in enumerate(lines, 1):
        print_formatted_event(idx, line)
    print(f"\n✅ Total {len(lines)} messages fetched successfully.\n")

def stream_live(topic_name):
    print(f"\n🔴 [LIVE STREAM] Listening to topic '{topic_name}' in real time...")
    print("⚡ UI / Backend-la action nadantha உடனே JSON message inga print aagum.")
    print("🛑 Press Ctrl + C to stop listening.\n")
    
    cmd = [
        "docker", "exec", "-i", "fieldops-kafka",
        "/opt/kafka/bin/kafka-console-consumer.sh",
        "--bootstrap-server", "localhost:9092",
        "--topic", topic_name
    ]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
        count = 0
        for line in proc.stdout:
            if line.strip().startswith("{"):
                count += 1
                print_formatted_event(count, line)
    except KeyboardInterrupt:
        print("\n\n👋 Stopped live listening.")
    except Exception as e:
        print(f"\n❌ Error streaming from Kafka: {e}")

def main():
    args = sys.argv[1:]
    if not args:
        show_topics()
        return

    first_arg = args[0].lower()
    
    if first_arg in ("topics", "list", "--list", "help", "-h"):
        show_topics()
        return
        
    if first_arg == "live":
        topic_key = args[1].lower() if len(args) > 1 else "job"
        topic_name = TOPIC_SHORTCUTS.get(topic_key, topic_key)
        stream_live(topic_name)
        return

    # User gave a topic directly (e.g. `python check_kafka_events.py job`)
    topic_name = TOPIC_SHORTCUTS.get(first_arg, first_arg)
    
    if len(args) > 1 and args[1].lower() == "live":
        stream_live(topic_name)
    else:
        limit = 5
        if len(args) > 1 and args[1].isdigit():
            limit = int(args[1])
        read_topic_history(topic_name, limit=limit)

if __name__ == "__main__":
    main()
