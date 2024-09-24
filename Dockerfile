FROM alpine:latest

RUN apk add --no-cache bash inotify-tools python3 py3-pip
RUN rm /usr/lib/python3.11/EXTERNALLY-MANAGED
RUN pip install requests jira python-dotenv
COPY .env /root
ENTRYPOINT [ "/root/inotify.sh" ]
