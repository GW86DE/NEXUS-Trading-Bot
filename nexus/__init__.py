"""NEXUS-Paketwurzel (10.8.0, Schritt 1 des Architektur-Umbaus).

Schichten (Abhaengigkeiten nur nach innen):

    interfaces  ->  application  ->  domain
    adapters    ->  ports/domain          (nie in Richtung Kern)
    state       ->  domain

Die Zuordnung jedes Moduls zu seiner Schicht steht in ``nexus/architektur/schichten.json``
und wird von ``tests/test_v1080_architektur.py`` gegen den echten Importgraphen geprueft.
Module wandern physisch in dieses Paket, sobald sie angefasst werden; bis dahin behalten
sie ihren alten Importpfad ueber eine ``sys.modules``-Weiche (siehe ``nexus/weiche.py``).
"""
