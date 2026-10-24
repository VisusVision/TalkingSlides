"""Conservative private inventory; cannot delete media."""
import json
from django.core.management.base import BaseCommand,CommandError
from core.visual_design.schemas import DesignError
from core.visual_segment_retention import private_inventory

class Command(BaseCommand):
    help='Bounded report-only private media inventory. No deletion or retention expiry.'
    def add_arguments(self,parser):parser.add_argument('--limit',type=int,default=1000)
    def handle(self,*args,**options):
        try:result=private_inventory(limit=options['limit'])
        except (DesignError,OSError) as exc:raise CommandError(getattr(exc,'code','private_storage_unavailable')) from exc
        self.stdout.write(json.dumps(result))
