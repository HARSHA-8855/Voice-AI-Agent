from celery import Celery

celery_app = Celery("campaigns", broker="redis://redis:6379/0")

@celery_app.task
def outbound_call_campaign(campaign_id):
    pass
