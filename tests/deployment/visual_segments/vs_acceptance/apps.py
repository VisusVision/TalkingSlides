from django.apps import AppConfig

class EngineeringOnlyConfig(AppConfig):
    name = 'vs_acceptance'
    def ready(self):
        from .dependencies import install
        install()
