# QGIS processing toolbox provider - TUFLOW Tools
# 

# -*- coding: utf-8 -*-
from qgis.core import QgsProcessingProvider
from qgis.PyQt.QtGui import QIcon
import os

from .algs.ts_qplot import TimeSeriesQPlotAlgorithm
from .algs.fh_classify import FloodHazardClassifyAlgorithm
from .algs.tuflow_log_monitor import TuflowLogMonitorAlgorithm
from .algs.sample_rasters import SampleRastersAlgorithm
from .algs.plugin_settings import PluginSettingsAlgorithm
from .algs.clear_memory import ClearMemoryAlgorithm
from .algs.batch_rename import RenameLayersByPattern
from .algs.restore_layer_name import RestoreLayerNameAlgorithm
from .algs.append_features import AppendFeaturesAlgorithm
from .algs.load_grid_output import LoadGridOutputAlgorithm
from .algs.load_po_lines import LoadPOLinesAlgorithm
from .algs.load_sample_points import LoadSamplePointsAlgorithm
from .algs.load_profile_sections import LoadProfileSectionsAlgorithm
from .algs.cross_section_alignment import CrossSectionAlignmentAlgorithm
from .algs.gis_location import GISLocationAlgorithm
from .algs.process_landcover import ProcessLandcoverAlgorithm
from .algs.wse_comparison import WSEComparisonAlgorithm
from .algs.lc_add_fields import LandCoverAddFieldsAlgorithm
from .algs.inundation_boundary import InundationBoundaryAlgorithm

class TuflowProcessingProvider(QgsProcessingProvider):
    PROVIDER_ID = 'tuflow_tools'

    def id(self):
        return self.PROVIDER_ID

    def name(self):
        return 'TUFLOW Tools'

    def longName(self):
        return self.name()

    def icon(self):
        icon_path = os.path.join(os.path.dirname(__file__), "flood-icon.png")
        return QIcon(icon_path)

    def loadAlgorithms(self):
        self.addAlgorithm(PluginSettingsAlgorithm())  # plugin settings dialog
        self.addAlgorithm(ClearMemoryAlgorithm())     # clear memory and file locks
        self.addAlgorithm(RenameLayersByPattern())    # batch rename layers
        self.addAlgorithm(RestoreLayerNameAlgorithm()) # restore layer name from source
        self.addAlgorithm(GISLocationAlgorithm())
        self.addAlgorithm(LandCoverAddFieldsAlgorithm()) # add specialized fields to land cover
        self.addAlgorithm(ProcessLandcoverAlgorithm()) # process land cover layer
        self.addAlgorithm(LoadGridOutputAlgorithm())    # load TUFLOW grid output wizard
        self.addAlgorithm(LoadPOLinesAlgorithm())      # load TUFLOW PO line output
        self.addAlgorithm(LoadSamplePointsAlgorithm()) # load TUFLOW sample points output
        self.addAlgorithm(LoadProfileSectionsAlgorithm())  # generate section profiles for lines
        self.addAlgorithm(CrossSectionAlignmentAlgorithm()) # interactive cross section tool
        self.addAlgorithm(TimeSeriesQPlotAlgorithm())  # time series Q plot algorithm
        self.addAlgorithm(FloodHazardClassifyAlgorithm())  # classify flood hazard (0-3) from depth & velocity
        self.addAlgorithm(TuflowLogMonitorAlgorithm())  # monitor TUFLOW log file algorithm
        self.addAlgorithm(SampleRastersAlgorithm())  # sample multiple rasters at vertices
        self.addAlgorithm(WSEComparisonAlgorithm())  # compare WSE rasters
        self.addAlgorithm(InundationBoundaryAlgorithm())  # Trace flooded footprints from depths