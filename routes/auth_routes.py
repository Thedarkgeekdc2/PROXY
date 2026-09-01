from flask import Blueprint, render_template, request, redirect, url_for, session, flash

auth_bp = Blueprint('auth', __name__)

CREDENTIALS = {'username': 'Proxy', 'password': 'KVS1261'}


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if session.get('logged_in'):
        return redirect(url_for('dashboard.index'))
    if request.method == 'POST':
        u = request.form.get('username', '').strip()
        p = request.form.get('password', '').strip()
        if u == CREDENTIALS['username'] and p == CREDENTIALS['password']:
            session['logged_in'] = True
            session.permanent = True
            flash('Login successful! Welcome back.', 'success')
            return redirect(request.args.get('next') or url_for('dashboard.index'))
        flash('Invalid ID or Password. Please try again.', 'error')
    return render_template('login.html')


@auth_bp.route('/logout')
def logout():
    session.clear()
    flash('Logged out successfully.', 'info')
    return redirect(url_for('auth.login'))
