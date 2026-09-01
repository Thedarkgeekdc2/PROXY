import os
from flask import Flask, session, redirect, url_for, request
from config import Config
from database.models import db
from routes.upload_routes    import upload_bp
from routes.proxy_routes     import proxy_bp
from routes.merge_routes     import merge_bp
from routes.dashboard_routes import dashboard_bp
from routes.auth_routes      import auth_bp
from routes.tt_view          import tt_view_bp

PUBLIC_ENDPOINTS = {'auth.login', 'auth.logout', 'static'}


def create_app():
    base_dir        = os.path.dirname(os.path.abspath(__file__))
    template_folder = os.path.join(base_dir, 'templates')
    static_folder   = os.path.join(base_dir, 'static')

    app = Flask(
        __name__,
        template_folder=template_folder,
        static_folder=static_folder,
    )
    app.config.from_object(Config)

    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    db.init_app(app)

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(upload_bp)
    app.register_blueprint(proxy_bp)
    app.register_blueprint(merge_bp)
    app.register_blueprint(tt_view_bp)

    @app.before_request
    def require_login():
        if request.endpoint in PUBLIC_ENDPOINTS:
            return
        if not session.get('logged_in'):
            return redirect(url_for('auth.login', next=request.url))

    with app.app_context():
        try:
            db.create_all()
        except Exception:
            import traceback
            traceback.print_exc()

    return app


app = create_app()

if __name__ == '__main__':
    app.run(host="127.0.0.1", port=5050, debug=False, use_reloader=False)
