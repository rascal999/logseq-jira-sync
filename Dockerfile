FROM alpine:latest

RUN apk add --no-cache bash python3 py3-pip
RUN ls /usr/lib/
RUN rm /usr/lib/python3.12/EXTERNALLY-MANAGED
RUN pip install requests jira python-dotenv
ENTRYPOINT [ "/root/logseq_jira_epic_sync.py" ]
