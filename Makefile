.PHONY: help install dev api frontend

help:
	@printf '%s\n' 'make install   Install Python and frontend dependencies' 'make dev       Start both servers (Ctrl+C stops both)' 'make api       Start the Python API' 'make frontend  Start the Vite development server'

install:
	uv sync --all-groups
	npm --prefix frontend ci

dev:
	python3 scripts/dev_servers.py

api:
	uv run python -m src.web.server

frontend:
	npm --prefix frontend run dev
