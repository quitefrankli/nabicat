from web_app.data_interface import DataInterface


def get_all_data_interfaces() -> list[type[DataInterface]]:
    from web_app.sentinel.data_interface import DataInterface as SentinelDataInterface
    from web_app.todoist.data_interface import DataInterface as TodoistDataInterface
    from web_app.metrics.data_interface import DataInterface as MetricsDataInterface
    from web_app.tubio.data_interface import DataInterface as TubioDataInterface
    from web_app.file_store.data_interface import DataInterface as FileStoreDataInterface
    from web_app.loft.data_interface import DataInterface as LoftDataInterface

    return [
        TodoistDataInterface,
        MetricsDataInterface,
        TubioDataInterface,
        FileStoreDataInterface,
        LoftDataInterface,
        SentinelDataInterface,
    ]

