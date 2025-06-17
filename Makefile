install:
	pip3 install paramiko watchdog

run:
	python3 bin/ai_agent.py

clean:
	rm -rf logs/* __pycache__ bin/__pycache__ tests/__pycache__

logs:
	@cat logs/agent.log
