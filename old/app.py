from flask import Flask, request, jsonify, render_template
from old.agent4 import run_agent, get_all_threads, get_thread_history

app = Flask(__name__, static_url_path='/static', static_folder='static', template_folder='templates')


@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")


@app.route("/chat", methods=["POST"])
def chat():
    try:
        data = request.get_json(force=True)
        query = data.get("message", "").strip()
        thread_id = data.get("thread_id", "default").strip()

        if not query:
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": "The 'message' field is required.",
                        "data": None,
                    }
                ),
                400,
            )

        answer = run_agent(query, thread_id)

        return (
            jsonify(
                {
                    "status": "success",
                    "message": "Response generated successfully.",
                    "data": {"user_message": query, "assistant_response": answer},
                }
            ),
            200,
        )

    except Exception as e:
        return jsonify({"status": "error", "message": str(e), "data": None}), 500


@app.route("/api/history", methods=["GET"])
def history():
    return jsonify({
        "status": "success",
        "data": get_all_threads()
    })

@app.route("/api/history/<thread_id>", methods=["GET"])
def history_detail(thread_id):
    history = get_thread_history(thread_id)
    return jsonify({
         "status": "success",
         "data": history
    })


import os

if __name__ == "__main__":
    debug_mode = os.environ.get("FLASK_DEBUG", "False").lower() == "true"
    app.run(debug=debug_mode)
