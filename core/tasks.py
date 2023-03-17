from time import sleep

from celery import shared_task


@shared_task()
def testing():
    print("starting")
    sleep(2)
    print("finished")
