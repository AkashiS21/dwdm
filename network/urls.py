from django.urls import path

from . import views

urlpatterns = [
    path('', views.index, name='index'),
    path('upload_csv/', views.upload_csv, name='upload_csv'),

    path('generate_mock_data/', views.generate_mock_data, name='generate_mock_data'),

    path('redactor/', views.redactor, name='redactor'),

    path('save_topology/', views.save_topology, name='save_topology'),
]
